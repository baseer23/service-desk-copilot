"""Offline: MCP request timeouts keep the session alive; placement keeps relationships."""

import json
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import ClassVar

import backend.app.mcp.client as client_module
import httpx
import pytest
from backend.app.mcp.client import DeskMateMCPClient, MCPToolError
from backend.app.models.document_graph_models import DocumentGraphProposal, GraphPlacementPlan
from backend.app.services.graph_placement import GraphPlacementService
from backend.app.store.graph_repo import InMemoryGraphRepository
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, ErrorData, TextContent

pytestmark = pytest.mark.unit


class FakeSession:
    """Stands in for the MCP ClientSession; never starts a subprocess."""

    calls: ClassVar[list] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def initialize(self):
        pass

    async def call_tool(self, name, arguments, read_timeout_seconds=None, meta=None):
        FakeSession.calls.append((name, read_timeout_seconds))
        if name == "slow_write":
            raise McpError(ErrorData(code=httpx.codes.REQUEST_TIMEOUT, message="Timed out"))
        if name == "broken_tool":
            raise McpError(ErrorData(code=-32603, message="Internal error"))
        payload = {"tool": name, "status": "ok", "items": [], "result": {}}
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))])


class FakeTransportClient(DeskMateMCPClient):
    @asynccontextmanager
    async def transport(self):
        yield None, None


@pytest.mark.asyncio
async def test_request_timeout_is_tool_error_and_session_stays_open(settings, monkeypatch):
    monkeypatch.setattr(client_module, "CancellableClientSession", FakeSession)
    FakeSession.calls = []
    client = FakeTransportClient(settings)
    try:
        await client.call_tool("list_documents", {})
        session_task, connections = client._task, client.connection_count

        with pytest.raises(MCPToolError) as timeout:
            await client.call_tool("slow_write", {})
        assert timeout.value.code == "MCP_REQUEST_TIMEOUT" and timeout.value.status_code == 504

        with pytest.raises(MCPToolError) as failure:
            await client.call_tool("broken_tool", {})
        assert failure.value.code == "MCP_ERROR"

        await client.call_tool("list_documents", {})
        assert client._task is session_task and not session_task.done()
        assert client.connection_count == connections == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_long_running_ingestion_tools_get_900_second_timeout(settings, monkeypatch):
    monkeypatch.setattr(client_module, "CancellableClientSession", FakeSession)
    FakeSession.calls = []
    client = FakeTransportClient(settings)
    try:
        await client.call_tool("enrich_document_graph", {"document_id": "doc"})
        await client.call_tool("enrich_document_semantic_graph", {"document_id": "doc"})
        await client.call_tool("list_documents", {})
    finally:
        await client.close()
    timeouts = dict(FakeSession.calls)
    assert timeouts["enrich_document_graph"] == timedelta(seconds=900)
    assert timeouts["enrich_document_semantic_graph"] == timedelta(seconds=900)
    assert timeouts["list_documents"] is None


def test_missing_placement_becomes_document_scoped_entity_and_keeps_relationship():
    proposal = DocumentGraphProposal(
        document_id="doc-harbour",
        entities=[
            {"temporary_id": "e1", "name": "Harbour Authority", "entity_type": "Organization"},
            {"temporary_id": "e2", "name": "East Basin", "entity_type": "Location"},
        ],
        relationships=[{"source_temporary_id": "e1", "target_temporary_id": "e2",
                        "predicate": "manages", "confidence": 0.9}],
    )
    discovery = {"e1": {"candidates": []}, "e2": {"candidates": [{"id": "ke_known"}]}}
    # The model omitted e1 and proposed an unauthorized reuse target for e2.
    model_plan = GraphPlacementPlan(document_id="doc-harbour", placements=[
        {"temporary_id": "e2", "decision": "REUSE_EXISTING", "existing_entity_id": "ke_other",
         "reason": "Looks similar"},
    ])
    plan = GraphPlacementService._conservative_plan(proposal, model_plan, discovery)
    assert [p.decision for p in plan.placements] == ["CREATE_NEW", "CREATE_NEW"]
    assert plan.placements[0].reason.startswith("No valid placement decision")
    assert plan.placements[1].reason.startswith("The proposed reuse target")

    graph = InMemoryGraphRepository()
    result = GraphPlacementService(None, graph, None, None).apply_placement(plan, proposal)
    assert result["entities_created"] == 2 and result["unresolved"] == 0
    assert result["relationships_written"] == 1 and result["relationships_dropped"] == 0
    assert len(graph.knowledge_relationships) == 1
    # CREATE_NEW identity is scoped to this document, so nothing merges elsewhere.
    created = set(graph.entity_links)
    other = GraphPlacementService._conservative_plan(
        proposal.model_copy(update={"document_id": "doc-other"}),
        GraphPlacementPlan(document_id="doc-other", placements=[]), discovery,
    )
    GraphPlacementService(None, graph, None, None).apply_placement(
        other, proposal.model_copy(update={"document_id": "doc-other"})
    )
    assert len(set(graph.entity_links) - created) == 2
