"""Drive durable website crawl jobs in bounded MCP steps; resume them after restart."""

import asyncio
import logging

from backend.app.core.principal import Principal, principal_scope

logger = logging.getLogger(__name__)


async def advance_crawl_jobs(settings, harness, ingestion_wakeup) -> bool:
    """Run one bounded step of every active job; True while any job has more work."""
    busy = False
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
                    busy = busy or result.get("status") in {"queued", "running"}
            except Exception as exc:
                logger.warning(
                    "Website crawl step failed tenant=%s type=%s", tenant, type(exc).__name__
                )
    return busy


async def url_crawl_worker(settings, harness, wakeup: asyncio.Event, ingestion_wakeup):
    """Resume persisted jobs at startup, then run new ones when signalled."""
    while True:
        wakeup.clear()
        if await advance_crawl_jobs(settings, harness, ingestion_wakeup):
            await asyncio.sleep(0)
            continue
        await wakeup.wait()
