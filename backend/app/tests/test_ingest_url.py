"""Tests for the /ingest/url endpoint and crawler behaviour."""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

class FakeResponse:
    def __init__(self, url: str, text: str, *, status_code: int = 200, content_type: str = "text/html") -> None:
        self.url = url
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = {"Content-Type": content_type}


class FakeSession:
    def __init__(self, responses: Dict[str, FakeResponse]) -> None:
        self._responses = responses
        self.headers: Dict[str, str] = {}
        self.request_log: list[str] = []

    def get(self, url: str, timeout: float | int = 10):  # noqa: D401 - mimic requests API
        self.request_log.append(url)
        return self._responses.get(url, FakeResponse(url, "", status_code=404, content_type="text/plain"))


@pytest.fixture
def fake_site(monkeypatch):
    robots = "User-agent: *\nDisallow: /blocked"
    page_root = """
        <html>
            <head><title>Root</title><link rel=\"canonical\" href=\"https://example.com/start\" /></head>
            <body>
                <nav>Menu</nav>
                <article>
                    <h1>Reset MFA</h1>
                    <p>Follow these steps.</p>
                    <a href="/guide">Guide</a>
                </article>
            </body>
        </html>
    """
    page_guide = """
        <html>
            <head><title>Guide</title></head>
            <body>
                <main>
                    <h2>Guide Heading</h2>
                    <p>Detailed instructions.</p>
                    <a href="/blocked">Blocked</a>
                </main>
            </body>
        </html>
    """
    responses = {
        "https://example.com/robots.txt": FakeResponse("https://example.com/robots.txt", robots, content_type="text/plain"),
        "https://example.com/start": FakeResponse("https://example.com/start", page_root),
        "https://example.com/guide": FakeResponse("https://example.com/guide", page_guide),
        "https://example.com/blocked": FakeResponse("https://example.com/blocked", "<html><body>Blocked</body></html>"),
    }
    session = FakeSession(responses)

    def fake_session_factory(*_args, **_kwargs):  # noqa: D401
        return session

    monkeypatch.setattr("backend.app.services.url_crawler.requests.Session", fake_session_factory)
    return session


def test_url_ingest_disabled(make_client, tmp_path):
    client = make_client(
        {
            "MODEL_PROVIDER": "stub",
            "EMBED_PROVIDER": "stub",
            "CHROMA_DIR": tmp_path / "chroma",
            "ALLOW_URL_INGEST": "0",
        }
    )

    try:
        response = client.post("/ingest/url", json={"url": "https://example.com"})
    finally:
        client.close()

    assert response.status_code == 403
    body = response.json()
    assert body["detail"] == "URL ingestion is disabled"


def test_crawl_respects_robots_and_limits(fake_site, make_client, tmp_path):
    client = make_client(
        {
            "MODEL_PROVIDER": "stub",
            "EMBED_PROVIDER": "stub",
            "CHROMA_DIR": tmp_path / "chroma",
            "ALLOW_URL_INGEST": "1",
            "URL_MAX_TOTAL_CHARS": "5000",
        }
    )

    try:
        response = client.post(
            "/ingest/url",
            json={"url": "https://example.com/start", "max_depth": 1, "max_pages": 5},
        )
    finally:
        client.close()

    assert response.status_code == 200
    body = response.json()
    assert body["pages"] == 2
    assert "https://example.com/guide" in fake_site.request_log
    assert "https://example.com/blocked" not in fake_site.request_log


def test_main_content_extraction(fake_site, make_client, tmp_path):
    client = make_client(
        {
            "MODEL_PROVIDER": "stub",
            "EMBED_PROVIDER": "stub",
            "CHROMA_DIR": tmp_path / "chroma",
            "ALLOW_URL_INGEST": "1",
            "URL_RATE_LIMIT_SEC": "0",
        }
    )

    try:
        response = client.post(
            "/ingest/url",
            json={"url": "https://example.com/start", "max_depth": 0, "max_pages": 1},
        )
        import backend.app.main as main

        chunks = list(main.app.state.graph_repo.chunks.values())
    finally:
        client.close()

    body = response.json()
    assert body["pages"] == 1
    assert body["chunks"] >= 1
    assert body["entities"] >= 0
    combined_text = "\n".join(chunk.get("text", "") for chunk in chunks)
    assert "Reset MFA" in combined_text
    assert "Menu" not in combined_text


def test_url_ingest_end_to_end(fake_site, make_client, tmp_path):
    client = make_client(
        {
            "MODEL_PROVIDER": "stub",
            "EMBED_PROVIDER": "stub",
            "CHROMA_DIR": tmp_path / "chroma",
            "ALLOW_URL_INGEST": "1",
            "URL_RATE_LIMIT_SEC": "0",
            "URL_MAX_TOTAL_CHARS": "10000",
        }
    )

    try:
        response = client.post(
            "/ingest/url",
            json={"url": "https://example.com/start", "max_depth": 1, "max_pages": 2},
        )
        body = response.json()
    finally:
        graph_repo = None
        try:
            import backend.app.main as main
+
            graph_repo = main.app.state.graph_repo
        finally:
            client.close()

    assert response.status_code == 200
    assert body["pages"] == 2
    assert body["chunks"] > 0
    assert body["vector_count"] == body["chunks"]

    assert graph_repo is not None
    if hasattr(graph_repo, "documents"):
        assert len(graph_repo.documents) >= 2
