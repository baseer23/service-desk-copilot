"""Offline website ingestion: canonical origin, polite crawling, refusals and the PDF pipeline.

Every HTTP response is a FakeResponse, Azure Document Intelligence is a fake
client and time is a fake clock; DNS is pinned and no socket is opened.
"""

import asyncio
import json
from collections import Counter
from itertools import pairwise
from types import SimpleNamespace

import pytest
from backend.app.mcp import server
from backend.app.models.document_graph_models import (
    DocumentGraphProposal,
    EntityPlacement,
    GraphPlacementPlan,
)
from backend.app.models.dto import IngestUrlJobResponse
from backend.app.services import document_processing, url_crawler
from backend.app.services.local_documents import LocalDocumentRepository
from backend.app.services.web import merge_html_analysis
from backend.app.store.graph_repo import InMemoryGraphRepository
from backend.app.tests.test_ingest_url import FakeResponse, FakeSession
from backend.app.tests.test_website_crawl import ErrorResponse, call, html, run_job

pytestmark = pytest.mark.integration

APEX, WWW = "https://harbour.example", "https://www.harbour.example"


class Clock:
    """Wall and monotonic time that only moves when the crawler sleeps (or a test says)."""

    def __init__(self):
        self.now = 1_000_000.0
        self.sleeps = []

    def time(self):
        return self.now

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class TimedSession(FakeSession):
    """FakeSession that records when each request was made and can vary responses."""

    def __init__(self, responses, clock):
        import requests

        super().__init__(responses)
        self.clock, self.times = clock, []
        self.cookies = requests.cookies.RequestsCookieJar()  # as on a real Session

    def get(self, url, timeout=10, **kwargs):
        self.times.append((url, self.clock.now))
        value = self._responses.get(url)
        if callable(value):
            self.request_log.append(url)
            return value()
        if isinstance(value, list):
            self.request_log.append(url)
            return value.pop(0) if len(value) > 1 else value[0]
        return super().get(url, timeout, **kwargs)


def page(url, title, body, links=()):
    return FakeResponse(url, html(title, body, links))


def with_slash(responses, origin):
    responses[origin + "/"] = responses[origin]
    return responses


def robots(origin, text):
    return FakeResponse(origin + "/robots.txt", text, content_type="text/plain")


def redirect(url, location):
    response = FakeResponse(url, "", status_code=301)
    response.headers["Location"] = location
    return response


def forbidden(url, **headers):
    response = ErrorResponse(url, "<html><body>403 Forbidden</body></html>", status_code=403)
    response.headers.update(headers)
    return response


@pytest.fixture
def web(monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.web.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    clock = Clock()
    monkeypatch.setattr(url_crawler, "time", clock)
    state = SimpleNamespace(clock=clock, session=None)

    def serve(responses):
        state.session = TimedSession(responses, clock)
        monkeypatch.setattr(
            url_crawler, "requests", SimpleNamespace(Session=lambda *_a, **_k: state.session)
        )
        return state.session

    state.serve = serve
    return state


@pytest.fixture
def workspace(settings, tmp_path, monkeypatch):
    configured = settings.model_copy(
        update={
            "local_persistence_enabled": True,
            "data_platform": "local",
            "local_documents_dir": tmp_path / "documents",
            "allow_url_ingest": True,
            "enable_public_web_tools": True,
            "url_rate_limit_sec": 0,
            "url_max_page_chars": 5000,
            "url_crawler_contact": "https://operator.example/crawler",
        }
    )
    state = SimpleNamespace(settings=configured)

    def configure(**updates):
        state.settings = state.settings.model_copy(update=updates)
        state.repository = LocalDocumentRepository(state.settings)

    def restart():
        state.repository = LocalDocumentRepository(state.settings)

    monkeypatch.setattr(server, "get_settings", lambda: state.settings)
    monkeypatch.setattr(server, "documents", lambda: state.repository)
    state.configure, state.restart = configure, restart
    restart()
    return state


def start_job(url, **limits):
    return call(
        server.ingest_url,
        url=url,
        max_depth=limits.get("depth", 2),
        max_pages=limits.get("pages", 50),
    )["job_id"]


def saved_urls(workspace):
    return {d["canonical_url"] for d in workspace.repository.list_documents()}


def test_apex_redirect_is_adopted_before_sitemaps_so_pages_skip_redirects(web, workspace):
    session = web.serve(
        with_slash(
            {
                APEX + "/robots.txt": robots(
                    APEX, "User-agent: *\nDisallow:\nSitemap: " + APEX + "/sitemap.xml"
                ),
                APEX: redirect(APEX, WWW + "/"),
                WWW + "/robots.txt": robots(
                    WWW, "User-agent: *\nDisallow:\nSitemap: " + APEX + "/sitemap.xml"
                ),
                WWW: page(
                    WWW, "Home", "<h1>Harbour</h1><p>Welcome.</p>", [APEX + "/about", "/contact"]
                ),
                WWW + "/sitemap.xml": FakeResponse(
                    WWW + "/sitemap.xml",
                    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    f"<url><loc>{APEX}/listed</loc></url></urlset>",
                    content_type="application/xml",
                ),
                WWW + "/about": page(WWW + "/about", "About", "<p>About the harbour.</p>"),
                WWW + "/contact": page(WWW + "/contact", "Contact", "<p>Call the office.</p>"),
                WWW + "/listed": page(WWW + "/listed", "Listed", "<p>Only in the sitemap.</p>"),
            },
            WWW,
        )
    )
    summary = run_job(start_job(APEX + "/"))

    assert summary["status"] == "complete" and summary["sitemap_seeds"] == 1
    assert saved_urls(workspace) == {WWW, WWW + "/about", WWW + "/contact", WWW + "/listed"}
    # The apex is touched only for robots.txt and the one root fetch that redirected;
    # the sitemap and every page come straight from the canonical www origin.
    assert [url for url in session.request_log if url.startswith(APEX)] == [
        APEX + "/robots.txt",
        APEX,
    ]
    assert WWW + "/sitemap.xml" in session.request_log
    assert APEX + "/" not in session.request_log
    # The 301 is followed (not the www fallback), and the root is fetched once.
    assert session.request_log.count(WWW + "/") == 1 and WWW not in session.request_log
    assert session.headers["User-Agent"] == "DeskMateBot/1.0 (+https://operator.example/crawler)"
    assert session.headers["Accept"].startswith("text/html,application/xhtml+xml")
    assert session.headers["Accept-Language"]


def test_crawl_delay_from_robots_is_honoured_between_requests(web, workspace):
    origin = "https://slow.example"
    session = web.serve(
        with_slash(
            {
                origin + "/robots.txt": robots(origin, "User-agent: *\nCrawl-delay: 10\nDisallow:"),
                origin: page(origin, "Home", "<p>Home page.</p>", ["/a", "/b"]),
                origin + "/a": page(origin + "/a", "A", "<p>Page a.</p>"),
                origin + "/b": page(origin + "/b", "B", "<p>Page b.</p>"),
            },
            origin,
        )
    )
    summary = run_job(start_job(origin + "/"))

    assert summary["status"] == "complete" and summary["crawl_delay"] == 10
    paced = [moment for url, moment in session.times if not url.endswith("/robots.txt")]
    assert len(paced) >= 4  # root, sitemap, /a, /b
    assert all(later - earlier >= 10 for earlier, later in pairwise(paced))
    assert max(web.clock.sleeps) == pytest.approx(10)


def blocked_site(origin, failing):
    links = ["/a", "/b", "/c", "/d", "/e"]
    responses = with_slash(
        {
            origin + "/robots.txt": robots(origin, "User-agent: *\nDisallow:"),
            origin: page(origin, "Home", "<p>Home page.</p>", links),
            **{
                origin + path: page(origin + path, path, f"<p>Content {path}.</p>")
                for path in links
            },
        },
        origin,
    )
    responses.update(failing)
    return responses


def test_bot_challenge_stops_the_crawl_and_keeps_saved_pages(web, workspace):
    origin = "https://guarded.example"
    session = web.serve(
        blocked_site(
            origin,
            {
                origin + "/b": forbidden(origin + "/b", **{"cf-mitigated": "challenge"}),
            },
        )
    )
    session.cookies.set("cf_clearance", "challenge-token", domain="guarded.example")
    session.cookies.set("theme", "dark", domain="guarded.example")
    summary = run_job_until_stopped(start_job(origin + "/"))

    assert summary["status"] == "blocked_by_site"
    assert summary["blocked_by_site"]["reason"] == "bot_challenge"
    assert summary["pages_blocked"] == {"bot_challenge": 1}
    assert saved_urls(workspace) == {origin, origin + "/a"}  # pages saved earlier are kept
    assert not any(url.endswith(("/c", "/d", "/e")) for url in session.request_log)
    assert "doesn't bypass site protections" in summary["error_message"]
    assert call(server.list_url_crawl_jobs) == []  # the worker stops hammering the site
    # Bot-protection cookies are never kept or replayed; ordinary ones are untouched.
    assert {cookie.name for cookie in session.cookies} == {"theme"}


def test_three_consecutive_403s_stop_the_crawl_with_a_plain_explanation(web, workspace):
    origin = "https://refusing.example"
    session = web.serve(
        blocked_site(
            origin, {origin + path: forbidden(origin + path) for path in ("/b", "/c", "/d")}
        )
    )
    summary = run_job_until_stopped(start_job(origin + "/"))

    assert summary["status"] == "blocked_by_site" and summary["error_code"] == "BLOCKED_BY_SITE"
    assert summary["skipped_reasons"]["http_403"] == 3 and summary["pages_blocked"] == {
        "http_403": 3
    }
    assert summary["error_message"] == (
        "The website refused automated access to 3 pages (HTTP 403), so the crawl stopped. "
        "DeskMate doesn't bypass site protections."
    )
    assert saved_urls(workspace) == {origin, origin + "/a"}
    assert origin + "/e" not in session.request_log


def test_429_retry_after_resumes_later_even_after_a_restart(web, workspace, monkeypatch):
    monkeypatch.setattr(server, "CRAWL_STEP_SECONDS", 0)  # one page per step
    origin = "https://busy.example"
    limited = ErrorResponse(origin + "/a", "", status_code=429)
    limited.headers["Retry-After"] = "120"
    session = web.serve(
        with_slash(
            {
                origin + "/robots.txt": robots(origin, "User-agent: *\nDisallow:"),
                origin: page(origin, "Home", "<p>Home page.</p>", ["/a"]),
                origin + "/a": [limited, page(origin + "/a", "A", "<p>Page a.</p>")],
            },
            origin,
        )
    )
    job_id = start_job(origin + "/")
    first = call(server.advance_url_crawl, job_id=job_id)
    waiting = call(server.advance_url_crawl, job_id=job_id)
    assert waiting["status"] == "running" and waiting["resume_after"] > web.clock.now
    assert waiting["skipped_reasons"] == {}  # waiting, not skipped
    requests_while_waiting = len(session.request_log)
    call(server.advance_url_crawl, job_id=job_id)
    assert len(session.request_log) == requests_while_waiting  # nothing fetched early

    workspace.restart()
    web.clock.now += 121
    summary = run_job(job_id)
    assert first["pages_saved"] == 1 and summary["status"] == "complete"
    assert saved_urls(workspace) == {origin, origin + "/a"}
    assert "rate_limited" not in summary["skipped_reasons"]


def run_job_until_stopped(job_id):
    for _ in range(50):
        summary = call(server.advance_url_crawl, job_id=job_id)
        if summary["status"] not in {"queued", "running"}:
            return summary
    raise AssertionError("Crawl job did not stop")


# --- Azure Document Intelligence for saved web pages --------------------------------

FEES_HTML = html(
    "Fees",
    "<h1>Fees</h1><p>Annual mooring fees.</p><table><tr><th>Item</th><th>Cost</th></tr>"
    "<tr><td>Permit</td><td>$20</td></tr></table>",
    ["/", "/contact"],
)


class FakeDocumentIntelligence:
    """Returns a layout result shaped like azure-ai-documentintelligence's AnalyzeResult."""

    def __init__(self, table_cells=(("Item", "Cost"), ("Permit", "$20"))):
        self.calls, self.table_cells = [], table_cells

    def begin_analyze_document(self, model, **arguments):
        self.calls.append({"model": model, **{k: v for k, v in arguments.items() if k != "body"}})
        content = "Fees\nAnnual mooring fees.\nItem Cost Permit $20"
        cells = [
            SimpleNamespace(
                row_index=r,
                column_index=c,
                content=text,
                kind="content",
                spans=[],
                bounding_regions=[],
            )
            for r, row in enumerate(self.table_cells)
            for c, text in enumerate(row)
        ]
        result = SimpleNamespace(
            content=content,
            pages=[
                SimpleNamespace(
                    page_number=1, lines=[], spans=[SimpleNamespace(offset=0, length=len(content))]
                )
            ],
            paragraphs=[
                SimpleNamespace(content="Fees", role="title", bounding_regions=[], spans=[]),
                SimpleNamespace(
                    content="Annual mooring fees.", role=None, bounding_regions=[], spans=[]
                ),
            ],
            tables=[SimpleNamespace(cells=cells, bounding_regions=[], row_count=2, column_count=2)],
            figures=[],
        )
        return SimpleNamespace(result=lambda timeout=None: result, details={})

    def close(self):
        pass


@pytest.fixture
def azure(monkeypatch):
    client = FakeDocumentIntelligence()
    real = document_processing.AzureDocumentExtractor
    monkeypatch.setattr(
        document_processing,
        "AzureDocumentExtractor",
        lambda settings, records: real(settings, records, client=client),
    )
    return client


def fees_site(web):
    origin = "https://fees.example"
    return origin, web.serve(
        with_slash(
            {
                origin + "/robots.txt": robots(origin, "User-agent: *\nDisallow:"),
                origin: FakeResponse(origin, FEES_HTML),
            },
            origin,
        )
    )


def test_azure_layout_for_html_is_normalized_with_local_links_and_reused(web, workspace, azure):
    workspace.configure(
        url_azure_analysis=True, azure_document_endpoint="https://fake.cognitiveservices.azure.com/"
    )
    origin, _ = fees_site(web)
    summary = run_job(start_job(origin + "/", depth=0, pages=1))
    assert summary["azure_analysis"] == "enabled"
    document_id = summary["document_ids"][0]
    assert workspace.repository.get(document_id)["document_processing"] == "azure_di"

    state = workspace.repository.process(document_id)
    extraction = json.loads(
        workspace.repository.blobs.get(document_id, state["extraction_artifact"])
    )
    assert azure.calls == [
        {
            "model": "prebuilt-layout",
            "content_type": "text/html",
            "output_content_format": "markdown",
        }
    ]
    assert extraction["method"] == "document_intelligence"
    assert [(p["text"], p["role"]) for p in extraction["paragraphs"]] == [
        ("Fees", "title"),
        ("Annual mooring fees.", None),
    ]
    assert [s["title"] for s in extraction["sections"]] == ["Fees"]
    metadata = extraction["metadata"]
    # Azure returns no links: links, canonical URL and title come from the local parse.
    assert metadata["links"] == [origin + "/contact"] and metadata["canonical_url"] == origin
    assert metadata["tables_source"] == "azure" and metadata["page_units_estimate"] == 1
    assert (
        state["local_index_ready"] is True and state["extraction_method"] == "document_intelligence"
    )

    # Resuming reuses the saved analysis batch, and an unchanged re-crawl is a duplicate.
    workspace.repository.reprocess(document_id, document_processing="azure_di")
    workspace.repository.process(document_id)
    again = run_job(start_job(origin + "/", depth=0, pages=1))
    assert again["pages_duplicate"] == 1 and again["pages_saved"] == 0
    assert len(azure.calls) == 1


def test_azure_tables_that_disagree_with_the_page_keep_local_tables():
    from backend.app.services.web import html_extraction

    local = html_extraction(FEES_HTML, "https://fees.example")
    analysis = {
        "pages": [{"page": 1, "text": "Fees"}],
        "paragraphs": [{"text": "Fees", "role": "title"}],
        "tables": [{"cells": [{"row": 0, "column": 0, "text": "Item"}]}],
    }
    merged = merge_html_analysis(analysis, local)
    assert merged["tables"] == local["tables"]
    assert merged["metadata"]["tables_source"] == "local"
    assert merged["metadata"]["tables_note"] == "azure_tables_disagree_with_page"
    empty = merge_html_analysis({"paragraphs": []}, local)
    assert empty["method"] == "html_structure"
    assert empty["metadata"]["azure_analysis"] == "empty_result_local_extraction_kept"


@pytest.mark.parametrize(
    "updates, expected",
    [
        ({}, "disabled"),
        ({"url_azure_analysis": True, "azure_document_endpoint": ""}, "not_configured"),
    ],
)
def test_without_azure_analysis_pages_use_free_local_extraction(
    web, workspace, azure, updates, expected
):
    workspace.configure(**updates)
    origin, _ = fees_site(web)
    summary = run_job(start_job(origin + "/", depth=0, pages=1))
    document_id = summary["document_ids"][0]
    state = workspace.repository.process(document_id)
    assert summary["azure_analysis"] == expected
    assert (
        state["document_processing"] == "local" and state["extraction_method"] == "html_structure"
    )
    assert azure.calls == []


# --- The same durable pipeline as PDFs --------------------------------------------------


class ScriptedProvider:
    """Schema-valid semantic graph answers grounded in the supplied object IDs."""

    async def generate_structured(self, stage, context, schema):
        if schema is DocumentGraphProposal:
            document_id = context.get("document", {}).get("id") or context["document_id"]
            evidence = [item["object_id"] for item in context.get("objects", [])][:1]
            if not evidence:
                evidence = [
                    object_id
                    for section in context.get("section_analyses", [])
                    for entity in section.get("entities", [])
                    for object_id in entity["evidence_object_ids"]
                ][:1]
            return DocumentGraphProposal(
                document_id=document_id,
                summary="A harbour web page.",
                document_type="WebPage",
                entities=[
                    {
                        "temporary_id": "harbour",
                        "name": "Harbour Authority",
                        "entity_type": "Organization",
                        "evidence_object_ids": evidence,
                    }
                ],
            )
        if schema is GraphPlacementPlan:
            return GraphPlacementPlan(
                document_id=context.get("document_id", "doc"),
                placements=[
                    EntityPlacement(
                        temporary_id="harbour", decision="CREATE_NEW", reason="New entity."
                    )
                ],
            )
        raise AssertionError(f"Unexpected structured stage {stage}")


class PipelineHarness:
    """The document worker's MCP calls, served by the real local repository."""

    def __init__(self, workspace, graph):
        self.workspace, self.graph, self.calls = workspace, graph, []

    async def invoke(self, name, arguments):
        from backend.app.services.document_semantic_graph import DocumentSemanticGraphService

        self.calls.append((name, arguments))
        repository = self.workspace.repository
        if name == "list_documents":
            return {"items": repository.list_documents()}
        if name == "index_document":
            return {"result": repository.process(arguments["document_id"])}
        if name == "enrich_document_graph":
            return {"result": repository.enrich_graph(arguments["document_id"])}
        if name == "enrich_document_semantic_graph":
            service = DocumentSemanticGraphService(
                self.workspace.settings,
                repository,
                repository.records,
                ScriptedProvider(),
                self.graph,
                repository.search_store,
            )
            return {"result": await service.enrich(arguments["document_id"])}
        raise AssertionError(name)


def linked_site(web):
    origin = "https://linked.example"
    web.serve(
        with_slash(
            {
                origin + "/robots.txt": robots(origin, "User-agent: *\nDisallow:"),
                origin: page(
                    origin, "Home", "<h1>Harbour</h1><p>Welcome to the harbour.</p>", ["/a", "/b"]
                ),
                origin + "/a": page(
                    origin + "/a", "A", "<h1>Moorings</h1><p>Mooring rules.</p>", ["/b"]
                ),
                origin + "/b": page(origin + "/b", "B", "<h1>Fees</h1><p>Fee schedule.</p>", ["/"]),
            },
            origin,
        )
    )
    return origin


def test_every_saved_page_goes_through_the_pdf_pipeline_and_progress_matches(
    web, workspace, monkeypatch
):
    from backend.app.services.document_worker import process_document_pass

    graph = InMemoryGraphRepository()
    monkeypatch.setattr("backend.app.store.graph_repo.InMemoryGraphRepository", lambda: graph)
    workspace.configure(semantic_graph_ingestion_enabled=True)
    origin = linked_site(web)
    job_id = run_job(start_job(origin + "/"))["job_id"]

    harness = PipelineHarness(workspace, graph)
    for _ in range(30):
        if asyncio.run(process_document_pass(workspace.settings, harness)) is None:
            break
    documents = workspace.repository.list_documents()
    assert len(documents) == 3
    assert all(d["local_index_ready"] for d in documents)
    assert {d["graph_link_status"] for d in documents} == {"linked"}
    assert {d["semantic_graph_status"] for d in documents} <= {"linked", "linked_no_relationships"}
    steps = Counter(name for name, _ in harness.calls if name != "list_documents")
    assert steps["index_document"] == 3 and steps["enrich_document_semantic_graph"] == 3

    ids = {d["canonical_url"]: d["id"] for d in documents}
    home, a, b = ids[origin], ids[origin + "/a"], ids[origin + "/b"]
    assert graph.websites[origin] == {home, a, b}
    assert graph.document_links == {(home, a), (home, b), (a, b), (b, home)}

    summary = call(server.get_url_crawl_job, job_id=job_id)
    IngestUrlJobResponse.model_validate(summary)  # what the status endpoint returns
    assert summary["pages_fetched"] == summary["pages_saved"] == 3
    assert summary["pages_indexed"] == sum(bool(d["local_index_ready"]) for d in documents)
    assert summary["pages_linked"] == sum(d["graph_link_status"] == "linked" for d in documents)
    assert summary["pages_semantic"] == dict(Counter(d["semantic_graph_status"] for d in documents))
    assert summary["pages_failed"] == 0 and summary["pages_azure_analysed"] == 0


def test_links_to_is_written_once_both_pages_exist_and_counts_are_honest():
    graph = InMemoryGraphRepository()
    origin = "https://linked.example"
    assert (
        graph.link_website_document("missing", origin, origin + "/x", [])["document_found"] is False
    )
    graph.upsert_document("a", "A", canonical_url=origin + "/a")
    first = graph.link_website_document("a", origin, origin + "/a", [origin + "/b"])
    assert first == {"outbound": 0, "inbound": 0, "document_found": True}  # B not saved yet
    graph.upsert_document("b", "B", canonical_url=origin + "/b")
    second = graph.link_website_document("b", origin, origin + "/b", [])
    assert second == {"outbound": 0, "inbound": 1, "document_found": True}
    again = graph.link_website_document("a", origin, origin + "/a", [origin + "/b"])
    assert again["outbound"] == 0  # already written: nothing new is reported
    assert graph.document_links == {("a", "b")}


def test_recrawling_unchanged_pages_with_volatile_markup_creates_no_duplicates(web, workspace):
    origin = "https://nonce.example"
    served = {"count": 0}

    def volatile(url, title, body, links=()):
        anchors = "".join(f"<a href={link}>{link}</a>" for link in links)

        def respond():
            served["count"] += 1
            token = f"n{served['count']}"
            return FakeResponse(
                url,
                (
                    f"<html><head><title>{title}</title><meta name='csrf-token' content='{token}'>"
                    f"<script nonce='{token}'>var t='{token}'</script></head><body>"
                    f"<!-- generated {token} --><nav>{anchors}</nav>"
                    f"<main nonce='{token}'>{body}</main></body></html>"
                ),
            )

        return respond

    responses = {
        origin + "/robots.txt": robots(origin, "User-agent: *\nDisallow:"),
        origin: volatile(origin, "Home", "<p>Home page.</p>", ["/a"]),
        origin + "/a": volatile(origin + "/a", "A", "<p>Page a.</p>"),
    }
    responses[origin + "/"] = responses[origin]
    web.serve(responses)
    first = run_job(start_job(origin + "/"))
    second = run_job(start_job(origin + "/"))
    assert first["pages_saved"] == 2
    assert second["pages_saved"] == 0 and second["pages_duplicate"] == 2
    assert len(workspace.repository.list_documents()) == 2


@pytest.mark.asyncio
async def test_a_stalled_document_cannot_starve_newly_queued_pages():
    from backend.app.services.document_worker import document_worker
    from backend.app.tests.test_document_worker import AdvancingHarness, _wait_for_document_call

    harness = AdvancingHarness(
        [
            # Eligible for a structural retry, but the retry changes nothing.
            {
                "id": "stuck",
                "status": "graph_pending",
                "phase": "link",
                "chunks": 2,
                "local_index_ready": True,
                "graph_retry_attempts": 1,
                "graph_retry_after": 0,
            },
            {
                "id": "page-1",
                "status": "queued",
                "phase": "extract",
                "ingestion_mode": "structured_graph",
            },
            {
                "id": "page-2",
                "status": "queued",
                "phase": "extract",
                "ingestion_mode": "structured_graph",
            },
        ]
    )
    settings = SimpleNamespace(
        semantic_graph_ingestion_enabled=False,
        auth_allowed_tenants=["tenant-1"],
        voice_tenant_id="tenant-1",
    )
    task = asyncio.create_task(document_worker(settings, harness, asyncio.Event()))
    try:
        await asyncio.wait_for(_wait_for_document_call(harness, "page-2"), timeout=1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    indexed = [args["document_id"] for name, args in harness.calls if name == "index_document"]
    assert indexed == ["page-1", "page-2"]
    assert [name for name, args in harness.calls if args.get("document_id") == "stuck"] == [
        "enrich_document_graph"
    ]
