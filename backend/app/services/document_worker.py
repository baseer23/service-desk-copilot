"""Recover durable document work; never auto-repeat ambiguous paid requests."""

import asyncio
import time

from backend.app.core.principal import Principal, principal_scope

MAX_GRAPH_RETRIES = 5


async def process_document_pass(settings, harness, *, timestamp=None):
    """Advance at most one durable document job without repeating paid extraction."""
    current = time.time() if timestamp is None else timestamp
    documents = (await harness.invoke("list_documents", {}))["items"]
    for document in documents:
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
            await harness.invoke("enrich_document_graph", {"document_id": document["id"]})
            return "structural_graph"
        if (
            settings.semantic_graph_ingestion_enabled
            and status == "ready"
            and document.get("ingestion_mode") == "structured_graph"
            and document.get("local_index_ready")
            and document.get("graph_link_status") == "linked"
            and (
                document.get("semantic_graph_status", "pending") in {"pending", "placing"}
                or (
                    document.get("semantic_graph_status") == "failed"
                    and document.get("semantic_graph_retryable") is True
                )
            )
        ):
            await harness.invoke(
                "enrich_document_semantic_graph", {"document_id": document["id"]}
            )
            return "semantic_graph"
        if status in {"queued", "waiting"}:
            await harness.invoke("index_document", {"document_id": document["id"]})
            return "index"
    return None


async def document_worker(settings, harness):
    while True:
        for tenant in (settings.auth_allowed_tenants or [settings.voice_tenant_id]):
            with principal_scope(Principal(tenant, "document-worker")):
                try:
                    await process_document_pass(settings, harness)
                except (RuntimeError, ValueError):
                    pass
        await asyncio.sleep(10)
