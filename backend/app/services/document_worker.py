"""Recover durable document work; never auto-repeat ambiguous paid requests."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from backend.app.core.principal import Principal, current_principal, principal_scope

MAX_GRAPH_RETRIES = 5
MAX_IMMEDIATE_PASSES = 100
_document_locks: dict[tuple[str, str], asyncio.Lock] = {}
logger = logging.getLogger(__name__)


def _lock_tenant(settings) -> str:
    """Use request authority; tolerate minimal settings doubles in focused tests."""
    try:
        return current_principal(settings).tenant_id
    except AttributeError:
        return "test-tenant"


@asynccontextmanager
async def document_processing_lock(tenant_id: str, document_id: str):
    """Serialize paid/indexing work for one document in this application process."""
    key = (tenant_id, document_id)
    lock = _document_locks.setdefault(key, asyncio.Lock())
    try:
        async with lock:
            yield
    finally:
        if not lock.locked() and not getattr(lock, "_waiters", None):
            _document_locks.pop(key, None)


async def process_document_pass(settings, harness, *, timestamp=None, exclude=(), attempted=None):
    """Advance at most one durable document job without repeating paid extraction.

    Documents in ``exclude`` are passed over, and the processed document's id is
    appended to ``attempted``, so a caller can step past a document whose pass
    changed nothing instead of letting it starve the documents queued after it.
    """
    current = time.time() if timestamp is None else timestamp
    documents = (await harness.invoke("list_documents", {}))["items"]
    for document in documents:
        if document.get("id") in exclude:
            continue
        if attempted is not None:
            attempted.append(document.get("id"))
        phase = document.get("phase")
        status = document.get("status")
        if phase in {"extract_inflight", "embedding_inflight"}:
            continue
        if (
            phase == "link"
            and status in {"linking", "graph_pending"}
            and document.get("local_index_ready", bool(document.get("chunks")))
            and int(document.get("graph_retry_attempts", 0)) < MAX_GRAPH_RETRIES
            and float(document.get("graph_retry_after", 0) or 0) <= current
        ):
            tenant_id = _lock_tenant(settings)
            async with document_processing_lock(tenant_id, document["id"]):
                await harness.invoke("enrich_document_graph", {"document_id": document["id"]})
            return "structural_graph"
        if (
            settings.semantic_graph_ingestion_enabled
            and status == "ready"
            and document.get("ingestion_mode") == "structured_graph"
            and document.get("local_index_ready")
            and document.get("graph_link_status") == "linked"
            and (
                document.get("semantic_graph_status", "pending") in {
                    "pending", "understanding", "discovering", "placing",
                }
                or (
                    document.get("semantic_graph_status") == "failed"
                    and document.get("semantic_graph_retryable") is True
                )
            )
        ):
            tenant_id = _lock_tenant(settings)
            async with document_processing_lock(tenant_id, document["id"]):
                await harness.invoke(
                    "enrich_document_semantic_graph", {"document_id": document["id"]}
                )
            return "semantic_graph"
        if status in {"queued", "waiting"}:
            tenant_id = _lock_tenant(settings)
            async with document_processing_lock(tenant_id, document["id"]):
                await harness.invoke("index_document", {"document_id": document["id"]})
            return "index"
    return None


async def find_next_retry_deadline(settings, harness):
    """Return the earliest persisted, safe structural-graph retry deadline."""
    documents = (await harness.invoke("list_documents", {}))["items"]
    deadlines = [
        float(document.get("graph_retry_after", 0) or 0)
        for document in documents
        if document.get("phase") == "link"
        and document.get("status") in {"linking", "graph_pending"}
        and document.get("local_index_ready", bool(document.get("chunks")))
        and int(document.get("graph_retry_attempts", 0)) < MAX_GRAPH_RETRIES
        and document.get("phase") not in {"extract_inflight", "embedding_inflight"}
        and float(document.get("graph_retry_after", 0) or 0) > 0
    ]
    return min(deadlines) if deadlines else None


async def _work_fingerprint(harness):
    """Small persisted-state fingerprint used to stop unchanged jobs spinning."""
    documents = (await harness.invoke("list_documents", {}))["items"]
    return tuple(
        (
            row.get("id"), row.get("status"), row.get("phase"),
            row.get("local_index_ready"), row.get("graph_link_status"),
            row.get("graph_retry_attempts"), row.get("graph_retry_after"),
            row.get("semantic_graph_status"), row.get("semantic_graph_retryable"),
        )
        for row in documents
    )


async def document_worker(settings, harness, wakeup: asyncio.Event):
    """Recover at startup, then drain durable work on signals and retry deadlines."""
    while True:
        wakeup.clear()
        next_retry_at = None
        for tenant in (settings.auth_allowed_tenants or [settings.voice_tenant_id]):
            with principal_scope(Principal(tenant, "document-worker")):
                try:
                    stalled = set()
                    for _ in range(MAX_IMMEDIATE_PASSES):
                        before = await _work_fingerprint(harness)
                        attempted = []
                        if await process_document_pass(
                            settings, harness, exclude=stalled, attempted=attempted
                        ) is None:
                            break
                        if await _work_fingerprint(harness) == before:
                            # Set aside a document whose pass changed nothing and
                            # continue with the rest of the queue.
                            stalled.add(attempted[-1])
                    else:
                        # Yield between bounded batches while ensuring a large queue
                        # remains immediately runnable without an external signal.
                        wakeup.set()
                    next_tenant_retry = await find_next_retry_deadline(settings, harness)
                    if next_tenant_retry is not None:
                        next_retry_at = (
                            next_tenant_retry if next_retry_at is None
                            else min(next_retry_at, next_tenant_retry)
                        )
                except (RuntimeError, ValueError) as exc:
                    # Durable state records retryability. Await a fresh signal instead
                    # of spinning on an unchanged failing job.
                    logger.error(
                        "Document worker pass failed tenant=%s type=%s",
                        tenant, type(exc).__name__,
                    )
                    continue
        if wakeup.is_set():
            continue
        if next_retry_at is None:
            await wakeup.wait()
            continue
        try:
            await asyncio.wait_for(
                wakeup.wait(), timeout=max(0.05, next_retry_at - time.time())
            )
        except asyncio.TimeoutError:
            pass
