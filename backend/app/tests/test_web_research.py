from types import SimpleNamespace
import pytest
import requests
from backend.app.services.web import (
    validate_public_url,
    fetch_response,
    fetch_page,
    UnsafeURL,
    FetchError,
    extract_html,
    relevant_excerpt,
)
from backend.app.mcp import server
from backend.app.agent.models import InvestigationDecision
from backend.app.services.source_policy import rank_sources


def test_documentation_table_cells_are_preserved_without_duplicate_paragraphs():
    page, _ = extract_html(
        "<main><h1>Error reference</h1><table><tr><th>Code</th><th>Description</th></tr><tr><td>TEST53003</td><td><p>Policy blocked token issuance.</p></td></tr></table></main>",
        "https://example.com/errors",
    )
    assert "TEST53003 | Policy blocked token issuance." in page["text"]
    assert page["text"].count("Policy blocked token issuance.") == 1


def test_bounded_source_review_keeps_error_definition_deep_in_long_page():
    text = (
        "Documentation preface. " * 1000
        + "TEST53003: Policy blocked token issuance."
        + " Other entries." * 1000
    )
    excerpt = relevant_excerpt(text, "Vendor reference TEST53003", 1600)
    assert len(excerpt) <= 1600
    assert "TEST53003: Policy blocked token issuance." in excerpt
    assert "Documentation preface." in excerpt


def test_source_ranking_deduplicates_urls_and_prefers_authoritative_sources(settings):
    rows = rank_sources(
        [
            {"url": "https://blog.example/vpn", "title": "Blog"},
            {"url": "https://learn.microsoft.com/vpn", "title": "Microsoft"},
            {"url": "https://learn.microsoft.com/vpn#section", "title": "Duplicate"},
            {"url": "https://blog.example/vpn", "title": "Duplicate blog"},
        ],
        settings,
    )
    assert [row["title"] for row in rows] == ["Microsoft", "Blog"]


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/x",
        "http://127.0.0.1",
        "http://10.0.0.1",
        "http://192.168.1.1",
        "http://172.16.0.1",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]",
        "http://user:pass@example.com",
    ],
)
def test_reject_unsafe_urls(url):
    with pytest.raises(UnsafeURL):
        validate_public_url(url)


def response(url, body=b"", status=200, headers=None):
    r = requests.Response()
    r.status_code = status
    r.url = url
    r._content = body
    r._content_consumed = True
    r.headers.update(headers or {"Content-Type": "text/html"})
    return r


def public_dns(monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.web.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )


def test_redirect_rechecked_before_private_destination(monkeypatch):
    # Public DNS only for the original host; literal private redirect remains forbidden.
    monkeypatch.setattr(
        "backend.app.services.web.socket.getaddrinfo",
        lambda host, *a, **k: [
            (2, 1, 6, "", ("127.0.0.1" if host == "127.0.0.1" else "93.184.216.34", 443))
        ],
    )
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        assert kwargs["allow_redirects"] is False
        return response(url, status=302, headers={"Location": "http://127.0.0.1/secret"})

    with pytest.raises(UnsafeURL):
        fetch_response("https://example.com", SimpleNamespace(get=get))
    assert calls == ["https://example.com"]


def test_size_limit_and_canonical_extraction(monkeypatch):
    public_dns(monkeypatch)
    oversized = SimpleNamespace(get=lambda url, **k: response(url, b"x" * 1025))
    with pytest.raises(FetchError, match="size"):
        fetch_response("https://example.com", oversized, max_bytes=1024)
    html = b"<html><title>Guide</title><nav>Remove this</nav><main><h1>VPN guide</h1><p>Useful public content.</p></main></html>"
    page = fetch_page(
        "https://example.com/guide", SimpleNamespace(get=lambda url, **k: response(url, html))
    )
    assert (
        page["title"] == "Guide"
        and "Useful public content" in page["text"]
        and "Remove this" not in page["text"]
    )


@pytest.mark.asyncio
async def test_search_and_fetch_server_units(monkeypatch):
    import ddgs

    monkeypatch.setattr(
        ddgs,
        "DDGS",
        lambda **kwargs: SimpleNamespace(
            text=lambda query, **k: [
                {"title": "Guide", "href": "https://example.com", "body": "Useful result"}
            ]
        ),
    )
    hits = (await server.search_web("VPN guide")).result
    assert hits[0]["source_system"] == "public_web"
    monkeypatch.setattr(
        "backend.app.services.web.fetch_page",
        lambda *a, **k: {"url": a[0], "title": "Guide", "text": "Body"},
    )
    assert (await server.fetch_web_page("https://example.com")).result["text"] == "Body"


@pytest.mark.asyncio
async def test_news_search_deduplicates_and_preserves_valid_metadata(monkeypatch):
    import ddgs

    captured = {}

    class FakeDDGS:
        def __init__(self, **kwargs):
            captured["init"] = kwargs

        def news(self, query, **kwargs):
            captured["query"], captured["kwargs"] = query, kwargs
            return [
                {
                    "title": "Alberta update",
                    "url": "https://news.example/alberta",
                    "body": "A current provincial update.",
                    "date": "2026-10-18T12:00:00Z",
                    "source": "Example News",
                },
                {"title": "Duplicate", "url": "https://news.example/alberta"},
                {
                    "title": "Bad date",
                    "url": "https://news.example/second",
                    "date": "yesterday-ish",
                },
                {"title": "Unsafe", "url": "file:///etc/passwd"},
            ]

    monkeypatch.setattr(ddgs, "DDGS", FakeDDGS)
    result = (await server.search_news("Alberta news", max_results=4, timelimit="d")).result
    assert captured == {
        "init": {"timeout": 10},
        "query": "Alberta news",
        "kwargs": {"region": "ca-en", "timelimit": "d", "max_results": 4},
    }
    assert [row["title"] for row in result] == ["Alberta update", "Bad date"]
    assert result[0]["publisher"] == "Example News"
    assert result[0]["published_at"] == "2026-10-18T12:00:00Z"
    assert result[1]["published_at"] is None


@pytest.mark.asyncio
async def test_news_search_bounds_and_current_time_schema():
    assert (await server.search_news("Alberta", max_results=0)).status == "error"
    assert (await server.search_news("Alberta", timelimit="year")).status == "error"
    current = (await server.get_current_time(timezone="America/Halifax")).result
    assert current["timezone"] == "America/Halifax"
    assert current["timestamp"].startswith(current["date"])
    assert current["weekday"]
    assert (await server.get_current_time(timezone="Not/A_Zone")).status == "error"


def test_empty_local_knowledge_research_keeps_public_evidence_ephemeral(make_client):
    c = make_client()
    harness = c.app.state.agent.harness
    original = harness.invoke

    async def web(name, arguments):
        if name == "search_web":
            return [{"url": "https://example.com/guide", "title": "Guide"}]
        if name == "fetch_web_page":
            return {
                "url": arguments["url"],
                "title": "Recovery guide",
                "text": "Review the diagnostic logs and verify the supported client configuration. "
                * 5,
                "source_system": "public_web",
            }
        return await original(name, arguments)

    harness.invoke = web
    cid = c.post(
        "/cases", json={"summary": "A newly released utility crashes during launch."}
    ).json()["case_id"]
    result = c.post(f"/cases/{cid}/investigate")
    assert result.status_code == 200
    state = result.json()
    assert state["status"] == "assessment_ready"
    body = result.json()
    assert any(e["tool"] == "fetch_web_page" for e in body["evidence"])
    context = c.portal.call(harness.invoke, "list_available_context", {})
    assert context["result"]["knowledge_chunks"] == 0
    assert not any(e["tool"] == "ingest_text" for e in c.get(f"/cases/{cid}/events").json())


def test_malformed_mcp_search_result_becomes_error_evidence_instead_of_crashing(make_client):
    c = make_client()
    harness = c.app.state.agent.harness
    original = harness.invoke

    async def malformed(name, arguments):
        if name == "search_web":
            return {"items": "not-a-list", "source_system": "public_web"}
        return await original(name, arguments)

    harness.invoke = malformed
    cid = c.post("/cases", json={"summary": "A fictional client fails with an unknown code."}).json()["case_id"]
    response = c.post(f"/cases/{cid}/investigate")
    assert response.status_code == 200
    assert any(
        row["tool"] == "search_web" and row["source_type"] == "tool_error"
        for row in response.json()["evidence"]
    )


def test_loop_limit_finalizes_without_recursion_failure(make_client):
    c = make_client({"MAX_INVESTIGATION_CYCLES": "3"})
    provider = c.app.state.agent.provider
    original = provider.generate_structured

    async def loop(stage, context, schema):
        if stage != "reason":
            return await original(stage, context, schema)
        return InvestigationDecision(
            evidence_summary="No conclusive evidence",
            enough_information=False,
            confidence=0,
            requested_tools=[{"name": "get_company_context", "arguments": {"company": "Unknown"}}],
        )

    provider.generate_structured = loop
    cid = c.post("/cases", json={"summary": "Unknown incident"}).json()["case_id"]
    result = c.post(f"/cases/{cid}/investigate")
    assert result.status_code == 200
    body = result.json()
    assert body["status"] == "needs_input"
    assert 1 <= body["research_iteration"] <= 3
    assert body["confidence"] == 0
    assert body["pending_question"] == (
        "Which application or service is affected, and what exact error or symptom do you see?"
    )


def test_verified_news_answer_survives_research_cycle_limit(make_client):
    client = make_client({"MAX_INVESTIGATION_CYCLES": "1"})
    agent = client.app.state.agent
    evidence = agent._evidence(
        {"case_id": "news-limit", "persist_case": False},
        "fetch_web_page",
        {
            "url": "https://news.example/alberta",
            "title": "Verified Alberta update",
            "text": "A fetched and independently verified Alberta report. " * 4,
            "published_at": "2026-10-18T12:00:00Z",
            "source_system": "public_web",
        },
    )
    answer = "Verified Alberta update — 2026-10-18 — https://news.example/alberta"
    result = client.portal.call(
        agent.finalize,
        {
            "case_id": "news-limit", "persist_case": False,
            "ticket": {"summary": "Alberta news"},
            "intent": {"knowledge_source": "public_web", "public_topic": "news"},
            "research_iteration": 1, "evidence": evidence, "chat_messages": [],
            "decision": InvestigationDecision(
                outcome="answer", evidence_summary="Fetched article supports the headline.",
                answer=answer, enough_information=True, confidence=0.8,
            ).model_dump(),
            "verification": {
                "supported_claims": [answer], "unsupported_claims": [],
                "contradictions": [], "missing_evidence": [],
                "evidence_ids": [evidence[0]["id"]], "confidence": 0.8,
                "should_continue_research": False, "justification": "Fetched evidence supports it.",
            },
        },
    )
    assert result["status"] == "assessment_ready"
    assert result["final_answer"] == answer


@pytest.mark.parametrize("selected_irrelevant", [False, True])
def test_fetched_irrelevant_page_is_not_ingested(make_client, selected_irrelevant):
    c = make_client()
    agent = c.app.state.agent
    original_invoke = agent.harness.invoke
    original_generate = agent.provider.generate_structured
    selections = []
    calls = []

    async def web(name, arguments):
        calls.append(name)
        if name == "search_web":
            return [{"url": "https://example.com/guide", "title": "Utility guide"}]
        if name == "fetch_web_page":
            return {"url": arguments["url"], "text": "Unrelated advertising content. " * 10}
        return await original_invoke(name, arguments)

    async def select(stage, context, schema):
        if stage == "evaluate_sources":
            selections.append(context)
            if context.get("purpose"):
                return schema(
                    selected_urls=["https://example.com/guide"] if selected_irrelevant else [],
                    directly_relevant=False,
                    evidence_gap="No recovery instructions in the fetched page.",
                    justification="Actual content is irrelevant despite the search title.",
                )
        return await original_generate(stage, context, schema)

    agent.harness.invoke, agent.provider.generate_structured = web, select
    cid = c.post("/cases", json={"summary": "A newly released utility crashes."}).json()["case_id"]
    result = c.post(f"/cases/{cid}/investigate")
    assert result.status_code == 200
    assert len(selections) == 1
    assert "fetch_web_page" in calls and "ingest_text" not in calls
    assert calls.index("fetch_web_page") < len(calls) - 1
    context = c.portal.call(original_invoke, "list_available_context", {})
    assert context["result"]["knowledge_chunks"] == 0


def test_failed_public_research_records_error_evidence(make_client):
    from backend.app.mcp.client import MCPToolError

    c = make_client()
    harness = c.app.state.agent.harness
    original = harness.invoke

    async def unavailable(name, arguments):
        if name == "search_web":
            raise MCPToolError("Public search unavailable")
        return await original(name, arguments)

    harness.invoke = unavailable
    cid = c.post("/cases", json={"summary": "An unknown utility crashes."}).json()["case_id"]
    result = c.post(f"/cases/{cid}/investigate")
    assert result.status_code == 200
    state = result.json()
    assert state["status"] == "insufficient_information" and state["confidence"] == 0
    assert any(
        e["tool"] == "search_web" and e["source_type"] == "tool_error" for e in state["evidence"]
    )


def test_empty_search_skips_source_evaluation(make_client):
    c = make_client()
    agent = c.app.state.agent
    original_invoke = agent.harness.invoke
    original_generate = agent.provider.generate_structured
    evaluated = []

    async def web(name, arguments):
        if name == "search_web":
            return []
        return await original_invoke(name, arguments)

    async def generate(stage, context, schema):
        if stage == "evaluate_sources":
            evaluated.append(context)
        return await original_generate(stage, context, schema)

    agent.harness.invoke, agent.provider.generate_structured = web, generate
    case_id = c.post("/cases", json={"summary": "A newly released utility crashes."}).json()[
        "case_id"
    ]
    assert c.post(f"/cases/{case_id}/investigate").status_code == 200
    assert evaluated == []


def test_unknown_selected_url_is_ignored_without_crashing(make_client):
    c = make_client()
    agent = c.app.state.agent
    original_invoke = agent.harness.invoke
    original_generate = agent.provider.generate_structured

    async def web(name, arguments):
        if name == "search_web":
            return [{"url": "https://example.com/guide", "title": "Utility guide"}]
        return await original_invoke(name, arguments)

    async def generate(stage, context, schema):
        if stage == "evaluate_sources":
            return schema(
                selected_urls=["https://invalid.example/not-a-result"],
                directly_relevant=True,
                evidence_gap="No valid selected source.",
                justification="The model returned an unknown URL.",
            )
        return await original_generate(stage, context, schema)

    agent.harness.invoke, agent.provider.generate_structured = web, generate
    case_id = c.post("/cases", json={"summary": "A newly released utility crashes."}).json()[
        "case_id"
    ]
    assert c.post(f"/cases/{case_id}/investigate").status_code == 200
