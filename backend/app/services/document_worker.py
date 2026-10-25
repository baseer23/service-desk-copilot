"""Recover durable document work; never auto-repeat ambiguous paid requests."""

import asyncio
from contextlib import suppress

from backend.app.core.principal import Principal, principal_scope


async def document_worker(settings, harness):
    while True:
        for tenant in (settings.auth_allowed_tenants or [settings.voice_tenant_id]):
            with principal_scope(Principal(tenant, "document-worker")):
                try:
                    documents = (await harness.invoke("list_documents", {}))["items"]
                    for document in documents:
                        if (
                            settings.semantic_graph_ingestion_enabled
                            and document.get("status") == "ready"
                            and document.get("ingestion_mode") == "structured_graph"
                            and document.get("local_index_ready")
                            and document.get("graph_link_status") == "linked"
                            and document.get("semantic_graph_status", "pending") in {"pending", "placing"}
                        ):
                            with suppress(RuntimeError):
                                await harness.invoke(
                                    "enrich_document_semantic_graph", {"document_id": document["id"]}
                                )
                            break
                        if document.get("status") not in {"queued", "waiting"}:
                            continue
                        if document.get("phase") in {"extract_inflight", "embedding_inflight"}:
                            continue
                        with suppress(RuntimeError):
                            await harness.invoke("index_document", {"document_id": document["id"]})
                        break
                except RuntimeError:
                    pass
        await asyncio.sleep(10)
