"""Offline whole-site crawl jobs: sitemaps, limits, resumable jobs and HTML structure.

Every response comes from the FakeSession/FakeResponse fixtures in
test_ingest_url.py; DNS is pinned to a public address and no socket is opened.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
import requests

from backend.app.mcp import server
from backend.app.services.local_documents import LocalDocumentRepository
from backend.app.tests.test_ingest_url import FakeResponse, FakeSession

pytestmark = pytest.mark.integration

ORIGIN = "https://site.example"
NUMBERED = [f"/page-{index}" for index in range(1, 13)]


class ErrorResponse(FakeResponse):
    """An HTTP error whose exception carries the response, like requests does."""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


def html(title, body, links=()):
    anchors = "".join(f'<a href="{link}">{link}</a>' for link in links)
    return (
        f"<html><head><title>{title}</title></head><body><nav>{anchors}</nav>"
        f"<main>{body}</main></body></html>"
    )


def site_responses():
    pages = {
        "/": html("Home", "<h1>Welcome</h1><p>Start here.</p>",
                  [*NUMBERED, "/same-a", "/same-b", "/fees", "/private/area", "/huge", "/busy"]),
        "/fees": html("Fees", "<h1>Fees</h1><p>Annual mooring fees.</p><h2>Schedule</h2>"
                      "<ul><li>Due in March</li></ul><table><tr><th>Item</th><th>Cost</th></tr>"
                      "<tr><td>Permit</td><td>$20</td></tr></table>", ["/"]),
        "/same-a": html("Same Title", "<p>Alpha harbour notice about kayaks.</p>", ["/same-b"]),
        "/same-b": html("Same Title", "<p>Bravo harbour notice about dinghies.</p>", ["/same-a"]),
        "/from-sitemap": html("Sitemap only", "<p>Only listed in the sitemap.</p>"),
        "/private/area": html("Private", "<p>Must not be fetched.</p>"),
        "/huge": html("Huge", "<p>" + "word " * 600 + "</p>"),
        **{path: html(f"Page {path}", f"<p>Content of {path} number section.</p>", ["/"])
           for path in NUMBERED},
    }
    responses = {ORIGIN + path.rstrip("/"): FakeResponse(ORIGIN + path.rstrip("/"), text)
                 for path, text in pages.items()}
    responses[ORIGIN + "/"] = responses[ORIGIN]
    responses[ORIGIN + "/busy"] = ErrorResponse(ORIGIN + "/busy", "", status_code=429)
    responses[ORIGIN + "/busy"].headers["Retry-After"] = "0"
    responses[ORIGIN + "/robots.txt"] = FakeResponse(
        ORIGIN + "/robots.txt",
        "User-agent: *\nDisallow: /private\nSitemap: https://site.example/sitemap-index.xml",
        content_type="text/plain",
    )
    responses[ORIGIN + "/sitemap-index.xml"] = FakeResponse(
        ORIGIN + "/sitemap-index.xml",
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<sitemap><loc>https://site.example/sitemap-pages.xml</loc></sitemap></sitemapindex>",
        content_type="application/xml",
    )
    responses[ORIGIN + "/sitemap-pages.xml"] = FakeResponse(
        ORIGIN + "/sitemap-pages.xml",
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://site.example/from-sitemap</loc></url>"
        "<url><loc>https://site.example/private/secret</loc></url>"
        "<url><loc>https://other.example/off-origin</loc></url></urlset>",
        content_type="application/xml",
    )
    return responses


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.web.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    session = FakeSession(site_responses())
    monkeypatch.setattr(
        "backend.app.services.url_crawler.requests",
        SimpleNamespace(Session=lambda *_args, **_kwargs: session),
    )
    return session


@pytest.fixture
def workspace(settings, tmp_path, monkeypatch):
    configured = settings.model_copy(update={
        "local_persistence_enabled": True, "data_platform": "local",
        "local_documents_dir": tmp_path / "documents",
        "allow_url_ingest": True, "enable_public_web_tools": True,
        "url_rate_limit_sec": 0, "url_max_page_chars": 1000,
    })
    state = SimpleNamespace(repository=LocalDocumentRepository(configured))
    monkeypatch.setattr(server, "get_settings", lambda: configured)
    monkeypatch.setattr(server, "documents", lambda: state.repository)

    def restart():
        # A new process: a fresh repository over the same durable stores.
        state.repository = LocalDocumentRepository(configured)

    state.restart = restart
    state.settings = configured
    return state


def call(tool, **arguments):
    result = asyncio.run(tool(**arguments))
    assert result.status == "ok", result.error
    return result.result


def run_job(job_id, limit=200):
    for _ in range(limit):
        summary = call(server.advance_url_crawl, job_id=job_id)
        if summary["status"] in {"complete", "failed"}:
            return summary
    raise AssertionError("Crawl job did not finish")


def test_job_returns_immediately_and_queues_pages_for_the_worker(site, workspace):
    job = call(server.ingest_url, url=ORIGIN + "/", max_depth=2, max_pages=50)
    assert job["status"] == "queued" and job["pages_saved"] == 0
    assert site.request_log == []  # Nothing is fetched until the background step.

    summary = run_job(job["job_id"])
    assert summary["status"] == "complete"
    documents = workspace.repository.list_documents()
    assert len(documents) == summary["pages_saved"] == len(summary["document_ids"])
    # Saved, not processed: the document worker indexes and links each page.
    assert {(d["status"], d["phase"], d["media_type"], d["source_type"]) for d in documents} == {
        ("queued", "extract", "text/html", "url")
    }


def test_sitemap_robots_limits_and_more_than_ten_pages(site, workspace):
    summary = run_job(call(server.ingest_url, url=ORIGIN + "/", max_depth=2, max_pages=50)["job_id"])
    saved = {d["canonical_url"] for d in workspace.repository.list_documents()}
    assert len(saved) > 10
    assert {ORIGIN + path for path in NUMBERED} <= saved
    assert ORIGIN + "/from-sitemap" in saved and summary["sitemap_seeds"] >= 1
    assert not any("private" in url for url in site.request_log)
    assert "https://other.example/off-origin" not in site.request_log
    reasons = summary["skipped_reasons"]
    # The linked private page is skipped; the sitemap-listed one is never queued.
    assert reasons.get("robots_disallowed") == 1
    assert reasons.get("too_large") == 1 and reasons.get("rate_limited") == 1
    assert ORIGIN + "/huge" not in saved and ORIGIN + "/busy" not in saved


def test_restart_resumes_job_without_refetching_or_duplicates(site, workspace, monkeypatch):
    monkeypatch.setattr(server, "CRAWL_STEP_SECONDS", 0)  # one page per step
    job_id = call(server.ingest_url, url=ORIGIN + "/", max_depth=2, max_pages=50)["job_id"]
    for _ in range(3):
        partial = call(server.advance_url_crawl, job_id=job_id)
    assert partial["status"] == "running" and partial["pages_saved"] == 3

    workspace.restart()
    summary = run_job(job_id)
    # Every page is fetched exactly once; only the rate-limited page is retried.
    pages = [
        url for url in site.request_log
        if "sitemap" not in url and "robots" not in url and not url.endswith("/busy")
    ]
    assert len(pages) == len(set(pages))
    assert summary["pages_duplicate"] == 0
    assert len(workspace.repository.list_documents()) == summary["pages_saved"]


def _crawl_and_index(workspace, max_pages=50):
    summary = run_job(call(server.ingest_url, url=ORIGIN + "/", max_depth=2, max_pages=max_pages)["job_id"])
    for document_id in summary["document_ids"]:
        workspace.repository.process(document_id)
    return summary


def test_same_title_pages_are_separate_and_both_searchable(site, workspace):
    _crawl_and_index(workspace)
    documents = {d["canonical_url"]: d for d in workspace.repository.list_documents()}
    a, b = documents[ORIGIN + "/same-a"], documents[ORIGIN + "/same-b"]
    assert a["title"] == b["title"] == "Same Title" and a["family"] != b["family"]
    texts = "\n".join(row["text"] for row in workspace.repository.search("harbour notice", 10))
    assert "Alpha harbour notice" in texts and "Bravo harbour notice" in texts


def test_saved_page_extraction_has_sections_paragraphs_and_tables(site, workspace):
    _crawl_and_index(workspace)
    fees = next(d for d in workspace.repository.list_documents() if d["canonical_url"] == ORIGIN + "/fees")
    state = workspace.repository.get(fees["id"])
    extraction = json.loads(workspace.repository.blobs.get(fees["id"], state["extraction_artifact"]))
    assert extraction["method"] == "html_structure"
    assert [s["title"] for s in extraction["sections"]] == ["Fees", "Schedule"]
    assert any(p["text"] == "Due in March" for p in extraction["paragraphs"])
    assert extraction["tables"][0]["cells"][3]["text"] == "$20"
    assert extraction["metadata"]["links"] == [ORIGIN]
    assert state["local_index_ready"] is True


def test_graph_gets_website_documents_links_and_url_provenance(site, workspace):
    from backend.app.store.graph_repo import InMemoryGraphRepository

    _crawl_and_index(workspace)
    graph = InMemoryGraphRepository()
    by_url = {}
    for document in workspace.repository.list_documents():
        state = workspace.repository.get(document["id"])
        extraction = json.loads(workspace.repository.blobs.get(document["id"], state["extraction_artifact"]))
        counts = workspace.repository._publish_reference_objects(
            graph, document["id"], state["title"], extraction, version=state["version"],
            provenance={key: state[key] for key in ("source_type", "source_url", "canonical_url")},
        )
        assert set(counts) == {"nodes", "relationships", "website_links"}
        by_url[state["canonical_url"]] = document["id"]
    assert graph.websites[ORIGIN] == set(by_url.values())
    a, b, home = by_url[ORIGIN + "/same-a"], by_url[ORIGIN + "/same-b"], by_url[ORIGIN]
    assert graph.documents[a]["source_type"] == "url"
    assert graph.documents[a]["source_url"] == graph.documents[a]["canonical_url"] == ORIGIN + "/same-a"
    # Pages link each other whichever was published first.
    assert {(a, b), (b, a), (home, a), (by_url[ORIGIN + "/fees"], home)} <= graph.document_links
    # Links to pages that were never saved (robots, too large) create nothing.
    assert all(target in by_url.values() for _, target in graph.document_links)


def test_chat_cites_web_page_title_and_url_not_page_number(make_client):
    client = make_client()
    agent = client.app.state.agent
    url = ORIGIN + "/fees"
    evidence = {
        "id": "web-fees", "source_system": "chroma", "source_type": "document",
        "tool": "search_knowledge", "timestamp": "2026-10-28T00:00:00Z",
        "observed_at": "2026-10-28T00:00:00Z", "source": "/documents/x/download", "url": url,
        "reliability": 0.9, "content": {
            "title": "Fees", "text": "Annual mooring fees are $20.",
            "metadata": {"source_type": "url", "url": url, "canonical_url": url, "page": 1},
        },
    }
    answer = "The annual mooring permit costs $20."
    state = {
        "case_id": "web-citation", "persist_case": False, "workflow": "knowledge",
        "ticket": {"summary": "How much is the mooring permit?"}, "research_iteration": 1,
        "evidence": [evidence], "confirmed_facts": [], "contradictions": [],
        "decision": {
            "outcome": "answer", "evidence_summary": "The fees page lists it.",
            "hypotheses": [{"claim": answer, "evidence_ids": ["web-fees"], "status": "supported"}],
            "enough_information": True, "answer": answer, "confidence": 0.9,
        },
        "verification": {
            "supported_claims": [answer], "evidence_ids": ["web-fees"], "confidence": 0.9,
            "should_continue_research": False, "justification": "Supported.",
        },
    }
    agent._request_events["web-citation"] = []
    result = client.portal.call(agent.finalize, state)
    final = result["final_answer"]
    assert final == f"The annual mooring permit costs $20 (Fees — {url})."
    assert "page" not in final
    assert url in result["final_response"]["sources"]
