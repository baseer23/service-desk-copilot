"""Drive durable website crawl jobs in bounded MCP steps; resume them after restart."""

import asyncio
import logging
import time

from backend.app.core.principal import Principal, principal_scope

logger = logging.getLogger(__name__)


async def advance_crawl_jobs(settings, harness, ingestion_wakeup):
    """Run one bounded step of every active job.

    Returns (busy, resume_at): busy while a job can make progress now, and the
    earliest time a job waiting out a site's Retry-After may continue.
    """
    busy, resume_at = False, None
    for tenant in (settings.auth_allowed_tenants or [settings.voice_tenant_id]):
        with principal_scope(Principal(tenant, "url-crawl-worker")):
            try:
                jobs = (await harness.invoke("list_url_crawl_jobs", {}))["items"]
                for job in jobs:
                    step = (await harness.invoke("advance_url_crawl", {"job_id": job["job_id"]}))
                    result = step.get("result") or {}
                    if result.get("saved_now"):
                        # Saved pages are indexed and linked by the document worker.
                        ingestion_wakeup.set()
                    if result.get("status") not in {"queued", "running"}:
                        continue
                    waiting_until = float(result.get("resume_after") or 0)
                    if waiting_until > time.time():
                        resume_at = min(resume_at or waiting_until, waiting_until)
                    else:
                        busy = True
            except Exception as exc:
                logger.warning(
                    "Website crawl step failed tenant=%s type=%s", tenant, type(exc).__name__
                )
    return busy, resume_at


async def url_crawl_worker(settings, harness, wakeup: asyncio.Event, ingestion_wakeup):
    """Resume persisted jobs at startup, then run new ones when signalled."""
    while True:
        wakeup.clear()
        busy, resume_at = await advance_crawl_jobs(settings, harness, ingestion_wakeup)
        if busy:
            await asyncio.sleep(0)
            continue
        if resume_at is None:
            await wakeup.wait()
            continue
        try:
            await asyncio.wait_for(wakeup.wait(), timeout=max(0.05, resume_at - time.time()))
        except TimeoutError:
            pass
