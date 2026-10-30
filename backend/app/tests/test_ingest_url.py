"""Tests for the /ingest/url endpoint and crawler behaviour."""

from __future__ import annotations

from typing import Dict

import pytest


class FakeResponse:
    def __init__(
        self, url: str, text: str, *, status_code: int = 200, content_type: str = "text/html"
    ) -> None:
        self.url = url
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = {"Content-Type": content_type}

    def close(self):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError()

    def iter_content(self, chunk_size):
        yield self.content


class FakeSession:
    def __init__(self, responses: Dict[str, FakeResponse]) -> None:
        self._responses = responses
        self.headers: Dict[str, str] = {}
        self.request_log: list[str] = []

    def get(self, url: str, timeout=10, **kwargs):  # noqa: D401 - mimic requests API
        self.request_log.append(url)
        return self._responses.get(
            url, FakeResponse(url, "", status_code=404, content_type="text/plain")
        )


@pytest.fixture
def fake_site(monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.web.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
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
        "https://example.com/robots.txt": FakeResponse(
            "https://example.com/robots.txt", robots, content_type="text/plain"
        ),
        "https://example.com/start": FakeResponse("https://example.com/start", page_root),
        "https://example.com/guide": FakeResponse("https://example.com/guide", page_guide),
        "https://example.com/blocked": FakeResponse(
            "https://example.com/blocked", "<html><body>Blocked</body></html>"
        ),
    }
    session = FakeSession(responses)

    def fake_session_factory(*_args, **_kwargs):  # noqa: D401
        return session

    from types import SimpleNamespace

    monkeypatch.setattr(
        "backend.app.services.url_crawler.requests", SimpleNamespace(Session=fake_session_factory)
    )
    return session


def bind_mocked_url_capability(client, monkeypatch):
    """Explicit network unit injection; storage remains real temporary Chroma."""
    from backend.app.mcp import server
    from backend.app.services.local_documents import LocalDocumentRepository

    settings = client.app.state.settings
    repository = LocalDocumentRepository(settings)
    monkeypatch.setattr(server, "get_settings", lambda: settings)
    monkeypatch.setattr(server, "documents", lambda: repository)
    original = client.app.state.mcp.call_tool

    async def call(name, arguments):
        if name == "ingest_url":
            return (await server.ingest_url(**arguments)).model_dump()
        return await original(name, arguments)

    monkeypatch.setattr(client.app.state.mcp, "call_tool", call)
    return repository


def test_url_ingest_disabled(make_client, tmp_path):
    client = make_client(
        {
            "GENERATION_PROVIDER": "stub",
            "EMBED_PROVIDER": "stub",
            "CHROMA_DIR": tmp_path / "chroma",
            "ALLOW_URL_INGEST": "0",
            "ENABLE_PUBLIC_WEB_TOOLS": "1",
            "LOCAL_PERSISTENCE_ENABLED": "true",
            "DATA_PLATFORM": "local",
        }
    )

    try:
        response = client.post("/ingest/url", json={"url": "https://example.com"})
    finally:
        client.close()

    assert response.status_code == 403
    body = response.json()
    assert body["detail"]["code"] == "PUBLIC_WEB_DISABLED"


def test_crawl_respects_robots_and_limits(fake_site, make_client, tmp_path, monkeypatch):
    client = make_client(
        {
            "GENERATION_PROVIDER": "stub",
            "EMBED_PROVIDER": "stub",
            "CHROMA_DIR": tmp_path / "chroma",
            "ALLOW_URL_INGEST": "1",
            "ENABLE_PUBLIC_WEB_TOOLS": "1",
            "LOCAL_PERSISTENCE_ENABLED": "true",
            "DATA_PLATFORM": "local",
            "URL_MAX_TOTAL_CHARS": "5000",
        }
    )

    bind_mocked_url_capability(client, monkeypatch)
    try:
        response = client.post(
            "/ingest/url",
            json={"url": "https://example.com/start", "max_depth": 1, "max_pages": 5},
        )
    finally:
        client.close()

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["pages"] == 2
    assert "https://example.com/guide" in fake_site.request_log
    assert "https://example.com/blocked" not in fake_site.request_log


def test_main_content_extraction(fake_site, make_client, tmp_path, monkeypatch):
    client = make_client(
        {
            "GENERATION_PROVIDER": "stub",
            "EMBED_PROVIDER": "stub",
            "CHROMA_DIR": tmp_path / "chroma",
            "ALLOW_URL_INGEST": "1",
            "ENABLE_PUBLIC_WEB_TOOLS": "1",
            "LOCAL_PERSISTENCE_ENABLED": "true",
            "DATA_PLATFORM": "local",
            "URL_RATE_LIMIT_SEC": "0",
        }
    )

    repository = bind_mocked_url_capability(client, monkeypatch)
    try:
        response = client.post(
            "/ingest/url",
            json={"url": "https://example.com/start", "max_depth": 0, "max_pages": 1},
        )

        matches = repository.search("Reset MFA", 5)
    finally:
        client.close()

    body = response.json()
    assert body["pages"] == 1
    assert body["chunks"] >= 1
    assert body["entities"] >= 0
    combined_text = "\n".join(match.get("text", "") for match in matches)
    assert "Reset MFA" in combined_text
    assert "Menu" not in combined_text


def test_url_ingest_end_to_end(fake_site, make_client, tmp_path, monkeypatch):
    client = make_client(
        {
            "GENERATION_PROVIDER": "stub",
            "EMBED_PROVIDER": "stub",
            "CHROMA_DIR": tmp_path / "chroma",
            "ALLOW_URL_INGEST": "1",
            "ENABLE_PUBLIC_WEB_TOOLS": "1",
            "URL_RATE_LIMIT_SEC": "0",
            "URL_MAX_TOTAL_CHARS": "10000",
            "LOCAL_PERSISTENCE_ENABLED": "true",
            "DATA_PLATFORM": "local",
        }
    )

    repository = bind_mocked_url_capability(client, monkeypatch)
    try:
        response = client.post(
            "/ingest/url",
            json={"url": "https://example.com/start", "max_depth": 1, "max_pages": 2},
        )
        body = response.json()

    finally:
        client.close()

    assert response.status_code == 200, response.json()
    assert body["pages"] == 2
    assert body["chunks"] > 0
    assert body["vector_count"] == body["chunks"]

    assert body["local_index_ready"] is True
    assert body["graph_link_status"] == "linked"
    assert len(repository.list_documents()) >= 2
