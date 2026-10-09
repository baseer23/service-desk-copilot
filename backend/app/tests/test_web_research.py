from types import SimpleNamespace
import pytest
import requests
from backend.app.services.web import (
    validate_public_url,
    fetch_response,
    fetch_page,
    UnsafeURL,
    FetchError,
)
from backend.app.mcp import server
from backend.app.agent.models import InvestigationDecision


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


def test_empty_local_knowledge_research_ingests_and_retrieves(make_client):
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
    body = result.json()
    assert any(e["tool"] == "fetch_web_page" for e in body["evidence"])
    assert any(
        e["tool"] == "search_knowledge" and e["content"]["metadata"]["source_type"] == "web"
        for e in body["evidence"]
    )
    context = c.portal.call(harness.invoke, "list_available_context", {})
    assert context["result"]["knowledge_chunks"] > 0
    assert any(e["tool"] == "ingest_text" for e in c.get(f"/cases/{cid}/events").json())


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
    assert body["status"] == "insufficient_information" and body["research_iteration"] == 3
    assert body["confidence"] == 0 and any("cycle limit" in u for u in body["uncertainty"])


def test_fetched_irrelevant_page_is_not_ingested(make_client):
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
                    selected_urls=[],
                    evidence_gap="No recovery instructions in the fetched page.",
                    justification="Actual content is irrelevant despite the search title.",
                )
        return await original_generate(stage, context, schema)

    agent.harness.invoke, agent.provider.generate_structured = web, select
    cid = c.post("/cases", json={"summary": "A newly released utility crashes."}).json()["case_id"]
    result = c.post(f"/cases/{cid}/investigate")
    assert result.status_code == 200
    assert len(selections) == 2 and "text" in selections[1]["results"][0]
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
    assert any(e["tool"] == "search_web" and e["source_type"] == "tool_error" for e in state["evidence"])
