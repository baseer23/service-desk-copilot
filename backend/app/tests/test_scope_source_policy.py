import pytest
from backend.app.agent.graph import ServiceDeskAgent
from backend.app.agent.models import RequestIntent
from backend.app.agent.scope import SCOPE_MESSAGE
from backend.app.services.source_policy import is_primary, permits, rank_sources


@pytest.mark.parametrize(
    "question",
    [
        "What is the latest on immigration in Canada?",
        "What is the sports score?",
        "Give me a restaurant recommendation",
        "What is the latest political news?",
    ],
)
def test_out_of_scope_rejected_before_mcp_or_web(make_client, question):
    client = make_client()
    agent = client.app.state.agent
    calls = []
    original = agent.harness.invoke

    async def record(name, args):
        calls.append(name)
        return await original(name, args)

    agent.harness.invoke = record
    response = client.post("/ask", json={"summary": question})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["case_id"] is None and data["result"]["final_answer"] == SCOPE_MESSAGE
    assert data["result"]["evidence"] == [] and calls == []


@pytest.mark.parametrize(
    "question",
    [
        "VPN is broken",
        "MFA reset failed",
        "Printer PRN-ACCT-02 cannot print",
        "What year did Abdul graduate in the uploaded resume?",
        "What does the internal password policy require?",
    ],
)
def test_support_and_document_scope_accepted(settings, question):
    import asyncio

    from backend.app.testing.provider_stub import StubProvider

    intent = asyncio.run(
        StubProvider().generate_structured("classify_intent", {"request": question}, RequestIntent)
    )
    assert intent.in_scope
    assert not (
        "keyword" in ServiceDeskAgent.classify_intent.__doc__
        if ServiceDeskAgent.classify_intent.__doc__
        else False
    )


def test_model_classification_is_authoritative_without_runtime_keyword_match(make_client):
    client = make_client()
    original = client.app.state.agent.provider.generate_structured

    async def classify(stage, context, schema):
        if stage == "classify_intent":
            return RequestIntent(
                in_scope=False,
                workflow="knowledge",
                goal="Unrelated topic",
                justification="Structured scope verdict",
            )
        return await original(stage, context, schema)

    client.app.state.agent.provider.generate_structured = classify
    result = client.post(
        "/ask", json={"summary": "No predefined keyword appears in this request"}
    ).json()
    assert result["result"]["final_answer"] == SCOPE_MESSAGE


def test_broad_scope_is_explicit_opt_in(make_client):
    client = make_client({"KNOWLEDGE_SCOPE": "broad"})
    response = client.post("/ask", json={"summary": "Latest immigration news"})
    assert response.status_code == 200
    assert response.json()["result"].get("final_answer") != SCOPE_MESSAGE


def test_source_ranking_strict_policy_and_suffix_spoofing(settings):
    rows = [
        {"url": "https://third-party.example/article"},
        {"url": "https://learn.microsoft.com/support"},
        {"url": "https://learn.microsoft.com.evil.example/article"},
    ]
    ranked = rank_sources(rows, settings)
    assert ranked[0]["url"] == "https://learn.microsoft.com/support"
    assert ranked[1]["source_quality"] == "third_party"
    strict = settings.model_copy(update={"research_source_policy": "primary_only"})
    assert len(rank_sources(rows, strict)) == 1
    assert not permits(rows[2]["url"], strict) and not is_primary(rows[2]["url"], settings)


def test_search_snippets_cannot_be_diagnostic_evidence():
    state = {
        "evidence": [
            {
                "source_type": "public_documentation",
                "tool": "search_web",
                "content": {"snippet": "A possible diagnosis"},
            }
        ]
    }
    assert not ServiceDeskAgent.usable_evidence(state)


def test_fresh_local_namespaces_preserve_legacy_and_isolated_vectors(settings):
    from backend.app.services.capabilities import knowledge_stores
    from backend.app.store.vector_chroma import VectorChromaStore

    legacy = VectorChromaStore(str(settings.chroma_dir))
    legacy.upsert([{
        "id": "retained", "text": "Previous unrelated knowledge",
        "metadata": {"doc_id": "legacy"}, "embedding": [1.0] * 384,
    }])
    isolated, _, _ = knowledge_stores(settings, connect_graph=False)
    isolated.upsert([{
        "id": "test", "text": "Fictional isolated knowledge",
        "metadata": {"embedding_provider": "stub", "embedding_model": "sha256-unit-vector-v1"},
        "embedding": [1.0] * 384,
    }])
    local = settings.model_copy(update={"embed_provider": "sentence_transformers", "embed_model_name": "all-MiniLM-L6-v2"})
    fresh, _, _ = knowledge_stores(local, connect_graph=False)
    assert fresh.count() == 0
    assert fresh.collection_name != isolated.collection_name
    assert fresh.collection_name != legacy.collection_name
    assert legacy.count() == isolated.count() == 1


def test_primary_only_blocks_final_redirect_in_planned_fetch(make_client):
    from backend.app.mcp.client import MCPToolError

    client = make_client({"RESEARCH_SOURCE_POLICY": "primary_only"})
    agent = client.app.state.agent

    async def redirected(name, arguments):
        return {"result": {"url": "https://third-party.example/article", "text": "Untrusted"}}

    agent.harness.invoke = redirected
    with pytest.raises(MCPToolError, match="Redirected public source excluded"):
        client.portal.call(
            agent._call, {}, "fetch_web_page", {"url": "https://learn.microsoft.com/guide"}
        )
