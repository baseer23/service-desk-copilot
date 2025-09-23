import os
import pytest

from backend.app.models.dto import (
    AskRequest,
    AskResponse,
    Citation,
    IngestPasteRequest,
    IngestPasteResponse,
    IngestPdfResponse,
)


def test_ingest_paste_request_requires_text():
    payload = IngestPasteRequest(text="hello world", title=None)
    assert payload.text == "hello world"
    assert payload.title is None


def test_ingest_paste_response_validates_counts():
    resp = IngestPasteResponse(chunks=2, entities=1, vector_count=2, ms=123)
    assert resp.chunks == 2
    assert resp.ms == 123


def test_ingest_pdf_response_fields():
    resp = IngestPdfResponse(pages=1, chunks=3, entities=2, vector_count=3, ms=456)
    assert resp.pages == 1
    assert resp.vector_count == 3


def test_citation_schema_roundtrip():
    citation = Citation(doc_id="doc-1", chunk_id="chunk-1", score=0.42, title="Manual")
    assert citation.score == pytest.approx(0.42)
    assert citation.title == "Manual"


def test_ask_request_default_top_k(monkeypatch):
    monkeypatch.delenv("TOP_K", raising=False)
    request = AskRequest(question="What is up?", top_k=None)
    assert request.top_k is None

    monkeypatch.setenv("TOP_K", "6")
    request_env = AskRequest(question="What is up?", top_k=None)
    assert request_env.top_k == 6


def test_ask_response_shape():
    response = AskResponse(
        answer="Test answer",
        provider="stub",
        question="Test question",
        citations=[Citation(doc_id="doc", chunk_id="chunk", score=0.9, title=None)],
        planner={"mode": "VECTOR"},
        latency_ms=120,
        confidence=0.8,
    )
    assert response.latency_ms == 120
    assert response.citations[0].doc_id == "doc"
