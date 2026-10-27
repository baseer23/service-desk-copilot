from types import SimpleNamespace

import pytest

from backend.app.services.document_worker import process_document_pass


class Harness:
    def __init__(self, documents):
        self.documents = documents
        self.calls = []

    async def invoke(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "list_documents":
            return {"items": self.documents}
        return {"result": {"status": "ok"}}


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
