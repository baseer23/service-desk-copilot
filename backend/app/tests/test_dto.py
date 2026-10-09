
from backend.app.models.dto import (
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
