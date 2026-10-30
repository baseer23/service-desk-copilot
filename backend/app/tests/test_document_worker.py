from types import SimpleNamespace

import asyncio

import pytest

from backend.app.services.document_worker import document_worker, process_document_pass


class Harness:
    def __init__(self, documents):
        self.documents = documents
        self.calls = []

    async def invoke(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "list_documents":
            return {"items": self.documents}
        return {"result": {"status": "ok"}}


class AdvancingHarness(Harness):
    async def invoke(self, name, arguments):
        result = await super().invoke(name, arguments)
        if name == "index_document":
            document = next(row for row in self.documents if row["id"] == arguments["document_id"])
            document.update({
                "status": "ready", "phase": "complete", "local_index_ready": True,
                "graph_link_status": "linked", "semantic_graph_status": "linked",
            })
        return result


@pytest.mark.asyncio
async def test_worker_recovers_graph_pending_from_saved_local_artifacts():
    harness = Harness([{
        "id": "doc-1", "status": "graph_pending", "phase": "link",
        "chunks": 2, "local_index_ready": True, "graph_retry_attempts": 1,
        "graph_retry_after": 90,
    }])
    result = await process_document_pass(
        SimpleNamespace(semantic_graph_ingestion_enabled=True), harness, timestamp=100
    )
    assert result == "structural_graph"
    assert harness.calls[-1] == ("enrich_document_graph", {"document_id": "doc-1"})


@pytest.mark.asyncio
async def test_worker_respects_graph_backoff_and_never_repeats_ambiguous_extraction():
    harness = Harness([
        {
            "id": "paid", "status": "error", "phase": "extract_inflight",
            "chunks": 0,
        },
        {
            "id": "backoff", "status": "graph_pending", "phase": "link",
            "chunks": 2, "local_index_ready": True, "graph_retry_attempts": 2,
            "graph_retry_after": 200,
        },
    ])
    result = await process_document_pass(
        SimpleNamespace(semantic_graph_ingestion_enabled=True), harness, timestamp=100
    )
    assert result is None
    assert harness.calls == [("list_documents", {})]


@pytest.mark.asyncio
async def test_worker_only_resumes_semantic_failures_marked_safe_to_retry():
    harness = Harness([{
        "id": "doc-2", "status": "ready", "phase": "complete",
        "ingestion_mode": "structured_graph", "local_index_ready": True,
        "graph_link_status": "linked", "semantic_graph_status": "failed",
        "semantic_graph_retryable": True,
    }])
    result = await process_document_pass(
        SimpleNamespace(semantic_graph_ingestion_enabled=True), harness, timestamp=100
    )
    assert result == "semantic_graph"
    assert harness.calls[-1] == (
        "enrich_document_semantic_graph", {"document_id": "doc-2"}
    )


@pytest.mark.asyncio
async def test_worker_recovers_at_startup_and_wakes_without_polling_delay():
    harness = AdvancingHarness([{
        "id": "doc-3", "status": "queued", "phase": "saved",
        "ingestion_mode": "structured_graph",
    }])
    settings = SimpleNamespace(
        semantic_graph_ingestion_enabled=True,
        auth_allowed_tenants=["tenant-1"],
        voice_tenant_id="tenant-1",
    )
    wakeup = asyncio.Event()
    task = asyncio.create_task(document_worker(settings, harness, wakeup))
    try:
        await asyncio.wait_for(_wait_for_call(harness, "index_document"), timeout=0.5)
        harness.documents.append({
            "id": "doc-4", "status": "queued", "phase": "saved",
            "ingestion_mode": "structured_graph",
        })
        wakeup.set()
        await asyncio.wait_for(_wait_for_document_call(harness, "doc-4"), timeout=0.5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _wait_for_call(harness, name):
    while not any(call_name == name for call_name, _ in harness.calls):
        await asyncio.sleep(0)


async def _wait_for_document_call(harness, document_id):
    while not any(arguments.get("document_id") == document_id for _, arguments in harness.calls):
        await asyncio.sleep(0)
