"""Offline graph-first saved-document retrieval with mocked Neo4j MCP responses.

Every store is a temporary Chroma/SQLite directory; Neo4j is either a fake
integration object or a real local stdio MCP server that imitates neo4j-mcp's
text-only JSON responses. No document here exists outside this test.
"""

import json
import sys
from types import SimpleNamespace

import pytest

from backend.app.agent.graph import ServiceDeskAgent
from backend.app.agent.models import (
    InvestigationDecision,
    InvestigationPlan,
    RequestIntent,
    VerificationResult,
)
from backend.app.core.principal import Principal, principal_scope
from backend.app.mcp.client import MCPToolError
from backend.app.mcp.multi import ExternalMCPClient, MultiMCPClient, ServerSpec

pytestmark = [pytest.mark.integration, pytest.mark.mcp]

DOC_ID = "doc-harbor-permit-450"
TITLE = "Harbor Permit 450.pdf"
PASSAGES = {
    "obj-overview": (2, "Harbor Permit 450 overview: the permit covers moorings in the east basin."),
    "obj-renewal": (7, "Renewal applications are due within 30 days of the expiry date."),
    "obj-fees": (9, "The annual mooring fee is listed in the schedule of charges."),
}
INVENTORY = [
    {"id": DOC_ID, "title": TITLE},
    {"id": "doc-test", "title": "test.pdf"},
    {"id": "doc-network", "title": "network.pdf"},
    {"id": "doc-vpn", "title": "VPN Setup Guide.docx"},
]


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("When is the harbor permit 450 renewal due?", [DOC_ID]),
        ("What does harborpermit450 say?", [DOC_ID]),
        ("Summarize HARBOR-PERMIT-450.", [DOC_ID]),
        ("What about harbor permit 4500?", []),
        ("What is the latest status?", []),
        ("Please run the test again", []),
        ("Summarize test.pdf", ["doc-test"]),
        ("The network is down in building B", []),
        ("What does the network document say?", ["doc-network"]),
        ("Walk me through the vpn setup guide", ["doc-vpn"]),
    ],
)
def test_alias_matching_uses_full_title_on_token_boundaries(question, expected):
    assert [m["id"] for m in ServiceDeskAgent._document_alias_matches(question, INVENTORY)] == expected


def test_alias_matching_resolves_document_family_to_newest_version():
    inventory = [
        {"id": "v1", "title": "Dock Rules 12.pdf", "family": "fam", "version": 1},
        {"id": "v2", "title": "Dock Rules 12.pdf", "family": "fam", "version": 2},
    ]
    matches = ServiceDeskAgent._document_alias_matches("dock rules 12 summary", inventory)
    assert [m["id"] for m in matches] == ["v2"]


CANDIDATES = [
    {"id": "aaaaaaaa11", "title": "Harbor Guide 2023.pdf", "alias": "harborguide2023", "version": 1},
    {"id": "bbbbbbbb22", "title": "Harbor Guide 2024.pdf", "alias": "harborguide2024", "version": 1},
]


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Harbor Guide 2023", "aaaaaaaa11"),
        ("the 2024 one", "bbbbbbbb22"),
        ("2", "bbbbbbbb22"),
        ("the first one", "aaaaaaaa11"),
        ("bbbbbbbb", "bbbbbbbb22"),
        ("not sure", None),
    ],
)
def test_clarification_answer_selects_one_candidate(answer, expected):
    chosen = ServiceDeskAgent._choose_document(answer, CANDIDATES)
    assert (chosen or {}).get("id") == expected


def test_graph_object_ids_prefer_semantic_support_then_page_order():
    rows = [{
        "mention_object_ids": ["m1", "p9"],
        "claim_object_ids": ["c1"],
        "page_objects": [
            {"object_id": "p9", "page": 9}, {"object_id": "p2", "page": 2},
            {"object_id": "p7", "page": 7}, {"object_id": None, "page": 1},
        ],
    }]
    assert ServiceDeskAgent._graph_object_ids(rows) == ["m1", "p9", "c1", "p2", "p7"]


def _agent(make_client):
    return make_client().app.state.agent


def test_out_of_scope_request_never_loads_document_inventory(make_client):
    client = make_client()
    agent = client.app.state.agent
    calls = []
    original = agent.harness.invoke

    async def record(name, arguments):
        calls.append(name)
        return await original(name, arguments)

    async def classified(stage, context, schema):
        return RequestIntent(
            workflow="knowledge", in_scope=False, goal="Unrelated request",
            justification="Outside the service-desk scope",
        )

    agent.harness.invoke = record
    agent.provider.generate_structured = classified
    response = client.post("/ask", json={"summary": "Recommend a restaurant near harbor permit 450"})
    assert response.status_code == 200, response.text
    assert "list_available_context" not in calls
    assert "list_documents" not in calls


def test_inventory_failure_continues_without_alias_resolution(make_client):
    client = make_client()
    agent = client.app.state.agent

    async def failing(name, arguments):
        raise MCPToolError("listing unavailable")

    agent.harness.invoke = failing
    update = client.portal.call(agent._load_saved_documents, {
        "ticket": {"summary": "Harbor Permit 450 renewal"},
    })
    assert update == {
        "available_context": None, "document_alias_matches": [],
        "resolved_document_ids": [], "graph_status": None,
    }


def test_ambiguous_document_is_asked_once_and_resolved_from_answer(make_client):
    client = make_client()
    agent = client.app.state.agent
    plans = []

    async def planner(stage, context, schema):
        assert stage == "plan"
        plans.append(context)
        return InvestigationPlan(
            goal="Answer from the selected saved document",
            tool_calls=[{
                "name": "search_knowledge",
                "arguments": {"question": "renewal", "top_k": 5, "mode": "HYBRID"},
                "reason": "The answer must come from the saved document.",
            }],
            stop_condition="Stop after document evidence is retrieved.",
        )

    agent.provider.generate_structured = planner
    state = {
        "case_id": "ambiguous-document", "persist_case": False, "workflow": "knowledge",
        "ticket": {"summary": "What does the harbor guide say about renewals?"},
        "intent": {"knowledge_source": "documents"}, "explicit_saved_knowledge": True,
        "document_alias_matches": CANDIDATES, "resolved_document_ids": [], "evidence": [],
    }
    asked = client.portal.call(agent.plan, state)["plan"]
    assert asked["clarification_needed"] and not asked["tool_calls"] and not plans
    question = asked["clarification_question"]
    assert "1) Harbor Guide 2023.pdf" in question and "2) Harbor Guide 2024.pdf" in question

    resolved = client.portal.call(agent.plan, {
        **state, "plan": asked, "plan_executed": True,
        "human_answers": [{"question": question, "answer": "the 2024 edition"}],
    })
    assert resolved["resolved_document_ids"] == ["bbbbbbbb22"]
    call = resolved["plan"]["tool_calls"][0]
    assert call["arguments"]["graph_plan"]["document_ids"] == ["bbbbbbbb22"]

    # An answer that still names no single candidate must not be asked again.
    unresolved = client.portal.call(agent.plan, {
        **state, "plan": asked, "plan_executed": True,
        "human_answers": [{"question": question, "answer": "whichever"}],
    })
    assert not unresolved["plan"]["clarification_needed"]
    assert unresolved["resolved_document_ids"] == ["aaaaaaaa11", "bbbbbbbb22"]


class FakeNeo4j:
    """Stands in for the official neo4j-mcp server's read-cypher tool."""

    spec = ServerSpec(name="neo4j", command="unused", read_tools=["read-cypher"],
                      workflows=["knowledge"])

    def __init__(self, rows=None, error=None):
        self.rows, self.error, self.calls = rows or [], error, []

    async def list_tools(self):
        return [{
            "name": "read-cypher", "description": "Run a read-only Cypher query",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "params": {"type": "object"}},
                "required": ["query"],
            },
            "annotations": {"readOnlyHint": True},
        }]

    async def call_tool(self, name, arguments):
        self.calls.append(arguments)
        if self.error:
            raise MCPToolError(self.error)
        # neo4j-mcp 1.6 answers with a text result holding a JSON array.
        text = SimpleNamespace(type="text", text=json.dumps(self.rows))
        return ExternalMCPClient._coerce_tool_result(
            SimpleNamespace(structuredContent=None, content=[text])
        )

    async def close(self):
        pass


def _connect_neo4j(client, fake):
    client.app.state.mcp.integrations["neo4j"] = fake
    client.portal.call(client.app.state.agent.harness.initialize)


GRAPH_ROW = {
    "document_id": DOC_ID, "title": TITLE, "summary": "Mooring permit rules",
    "entities": ["Harbor Permit 450", "Harbor Permit 450", "East Basin"],
    "mention_object_ids": [], "claim_object_ids": [],
    "page_objects": [
        {"object_id": object_id, "page": page} for object_id, (page, _) in PASSAGES.items()
    ],
}


@pytest.mark.parametrize(
    ("fake", "hydrated", "expected"),
    [
        (FakeNeo4j(rows=[]), None, "empty"),
        (FakeNeo4j(error="Neo4j syntax error"), None, "error"),
        (FakeNeo4j(rows=[GRAPH_ROW]), [], "stale"),
        (FakeNeo4j(rows=[GRAPH_ROW]), [{"id": "obj-renewal", "text": "x"}], "available"),
    ],
)
def test_graph_read_is_scoped_and_reports_explicit_status(make_client, fake, hydrated, expected):
    client = make_client()
    agent = client.app.state.agent
    _connect_neo4j(client, fake)
    original = agent.harness.invoke

    async def invoke(name, arguments):
        if name == "resolve_graph_references":
            return {"tool": name, "status": "ok", "items": hydrated, "result": hydrated,
                    "source_system": "chroma", "reliability": 0.9}
        return await original(name, arguments)

    agent.harness.invoke = invoke
    result = client.portal.call(agent._mandatory_saved_knowledge_graph_read, {
        "case_id": "graph-status", "persist_case": False,
        "ticket": {"summary": "When is the Harbor Permit 450 renewal due?"},
        "resolved_document_ids": [DOC_ID],
    })
    assert result["graph_status"] == expected
    arguments = fake.calls[0]
    assert arguments["params"]["document_ids"] == [DOC_ID]
    assert "$document_ids" in arguments["query"] and "$tenant_id" in arguments["query"]
    assert "OPTIONAL MATCH" not in arguments["query"]
    if expected == "error":
        assert result["output"]["status"] == "error"
    if expected in {"stale", "available"}:
        # Reference lists are hydration inputs; evidence keeps only a summary.
        assert result["output"]["items"][0]["entities"] == ["Harbor Permit 450", "East Basin"]
        assert result["output"]["items"][0]["referenced_objects"] == 3
        assert "page_objects" not in result["output"]["items"][0]


@pytest.mark.asyncio
async def test_neo4j_text_json_rows_empty_list_and_error_through_real_stdio(settings, tmp_path):
    source = tmp_path / "fake_neo4j_mcp.py"
    source.write_text('''import json
from mcp.server.fastmcp import FastMCP
server = FastMCP("fake-neo4j")
@server.tool(name="read-cypher", annotations={"readOnlyHint": True}, structured_output=False)
def read_cypher(query: str, params: dict | None = None) -> str:
    if query == "FAIL":
        raise RuntimeError("Neo4j rejected the query")
    if query == "EMPTY":
        return json.dumps([])
    return json.dumps([{"ok": 1}, {"ok": 2}])
server.run(transport="stdio")
''')
    configured = settings.model_copy(update={
        "neo4j_uri": "neo4j+s://fictional.example", "neo4j_user": "reader",
        "neo4j_password": __import__("pydantic").SecretStr("offline-only"),
        "neo4j_database": "neo4j",
        "mcp_servers": [{"name": "neo4j", "command": sys.executable, "args": [str(source)],
                         "read_tools": ["read-cypher"], "workflows": ["knowledge"]}],
    })

    class Builtin:
        _task = None
        connection_count = 0

        async def start(self):
            pass

        async def close(self):
            pass

        async def list_tools(self):
            return []

    multi = MultiMCPClient(configured, builtin=Builtin())
    try:
        await multi.list_tools()
        assert multi.integration_status == {"neo4j": "connected"}
        rows = await multi.call_tool("neo4j__read-cypher", {"query": "ROWS", "params": {}})
        assert rows["status"] == "ok" and rows["items"] == [{"ok": 1}, {"ok": 2}]
        empty = await multi.call_tool("neo4j__read-cypher", {"query": "EMPTY", "params": {}})
        assert empty["status"] == "ok" and empty["items"] == []
        with pytest.raises(MCPToolError):
            await multi.call_tool("neo4j__read-cypher", {"query": "FAIL", "params": {}})
    finally:
        await multi.close()


def _seed_document(tmp_path, monkeypatch):
    """Write one structured document to temporary SQLite/Chroma before startup."""
    env = {
        "LOCAL_PERSISTENCE_ENABLED": "true",
        "DATA_PLATFORM": "local",
        "CHROMA_DIR": str(tmp_path / "chroma"),
        "CASE_DB_PATH": str(tmp_path / "cases.db"),
        "LOCAL_DOCUMENTS_DIR": str(tmp_path / "documents"),
        "MAX_RESEARCH_ROUNDS": "0",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from backend.app.core.config import Settings
    from backend.app.services.local_documents import LocalDocumentRepository

    settings = Settings(_env_file=None)
    tenant = settings.auth_allowed_tenants[0] if settings.auth_allowed_tenants else settings.voice_tenant_id
    with principal_scope(Principal(tenant, "graph-first-seed", True)):
        repository = LocalDocumentRepository(settings)
        repository.records.write("document", DOC_ID, {
            "id": DOC_ID, "title": TITLE, "source_type": "pdf", "status": "ready",
            "phase": "complete", "pages": 9, "ingestion_mode": "structured_graph", "version": 1,
        })
        vector = repository.search_store.vector
        texts = [text for _, text in PASSAGES.values()]
        embeddings = repository.embeddings.embed_texts(texts)
        repository.search_store.upsert([
            {
                "id": f"{DOC_ID}:{object_id}",
                "text": text,
                "embedding": embedding,
                "metadata": {
                    "doc_id": DOC_ID, "object_id": object_id, "page": page, "title": TITLE,
                    "source_type": "pdf", "ingestion_mode": "structured_graph",
                    "embedding_provider": vector.embedding_provider,
                    "embedding_model": vector.embedding_model,
                },
            }
            for (object_id, (page, text)), embedding in zip(PASSAGES.items(), embeddings, strict=True)
        ])
    return env


@pytest.mark.parametrize("model_cites_evidence", [True, False])
def test_document_question_flows_graph_first_to_cited_answer(
    make_client, tmp_path, monkeypatch, model_cites_evidence
):
    env = _seed_document(tmp_path, monkeypatch)
    client = make_client(env)
    agent = client.app.state.agent
    fake = FakeNeo4j(rows=[GRAPH_ROW])
    _connect_neo4j(client, fake)
    calls = []
    original_invoke = agent.harness.invoke

    async def record(name, arguments):
        calls.append(name)
        return await original_invoke(name, arguments)

    agent.harness.invoke = record
    original_model = agent.provider.generate_structured
    answer = "Harbor Permit 450 renewals are due within 30 days of expiry."

    def renewal_ids(context):
        return [
            item["id"] for item in context["evidence"]
            if "30 days" in str(item["content"].get("text") or "")
        ][:1]

    async def model(stage, context, schema):
        if stage == "classify_intent":
            # The classifier misses the document; the saved-title match scopes it.
            return RequestIntent(workflow="knowledge", knowledge_source="general",
                                 goal="Answer the renewal deadline", justification="General question")
        if stage == "plan":
            return InvestigationPlan(
                goal="Find the renewal deadline",
                tool_calls=[{"name": "search_knowledge",
                             "arguments": {"question": "renewal deadline", "top_k": 5, "mode": "VECTOR"},
                             "reason": "The deadline is in the saved permit."}],
                stop_condition="Stop when the deadline passage is found.",
            )
        if stage == "reason":
            cited = renewal_ids(context) if model_cites_evidence else []
            return InvestigationDecision(
                outcome="answer", evidence_summary="The renewal passage states the deadline.",
                hypotheses=[{"claim": answer, "evidence_ids": cited, "status": "supported",
                             "confidence": 0.9}],
                enough_information=True, answer=answer, confidence=0.9,
            )
        if stage == "verify":
            return VerificationResult(
                supported_claims=[answer],
                evidence_ids=renewal_ids(context) if model_cites_evidence else [],
                confidence=0.9, should_continue_research=False, justification="Supported.",
            )
        return await original_model(stage, context, schema)

    agent.provider.generate_structured = model
    response = client.post("/ask", json={"summary": "When is the harbor permit 450 renewal due?"})
    assert response.status_code == 200, response.text
    result = response.json()["result"]

    # Document identification -> scoped Neo4j read -> local hydration -> local supplement.
    assert result["explicit_saved_knowledge"] is True
    assert fake.calls and fake.calls[0]["params"]["document_ids"] == [DOC_ID]
    assert calls.index("resolve_graph_references") < calls.index("search_knowledge")
    assert result["graph_status"] == "available"
    evidence = result["evidence"]
    graph_read = next(i for i, item in enumerate(evidence) if item["tool"] == "neo4j__read-cypher")
    documents = [i for i, item in enumerate(evidence) if item["source_type"] == "document"]
    assert documents and graph_read < documents[0]
    # Graph-hydrated passages are listed before any local-only supplement.
    hydrated = [
        i for i in documents
        if evidence[i]["content"].get("graph_enrichment") == "available"
    ]
    assert hydrated == documents[: len(hydrated)]
    # The evidence grader may drop unrelated graph objects (the fee passage);
    # the answer-bearing passage must arrive through the graph.
    graph_objects = {evidence[i]["content"]["metadata"]["object_id"] for i in hydrated}
    assert "obj-renewal" in graph_objects and graph_objects <= set(PASSAGES)

    final = result["final_answer"]
    if model_cites_evidence:
        assert final.endswith(f"({TITLE}, page 7).")
    else:
        assert "page" not in final
    assert "page 2" not in final
    # One inventory read per request (the background document worker shares
    # this harness and may list documents independently).
    assert calls.count("list_available_context") == 1
