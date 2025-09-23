"""PDF ingest should succeed when pdfminer is available."""

from __future__ import annotations

import pytest


try:  # pragma: no cover - exercised conditionally
    from pdfminer.high_level import extract_text  # noqa: F401 - import check
except ImportError:  # pragma: no cover
    extract_text = None


MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
    b"4 0 obj<</Length 55>>stream\n"
    b"BT /F1 24 Tf 72 100 Td (Hello PDF) Tj ET\n"
    b"endstream\nendobj\n"
    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
    b"xref\n0 6\n0000000000 65535 f \n0000000010 00000 n \n0000000059 00000 n \n0000000114 00000 n \n0000000223 00000 n \n0000000303 00000 n \n"
    b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n358\n%%EOF"
)


@pytest.mark.xfail(
    extract_text is None or getattr(extract_text, "__module__", "") != "pdfminer.high_level",
    reason="pdfminer.six not installed",
    strict=False,
)
def test_ingest_pdf_roundtrip(make_client, tmp_path):
    client = make_client(
        {
            "MODEL_PROVIDER": "stub",
            "EMBED_PROVIDER": "stub",
            "CHROMA_DIR": tmp_path / "chroma",
        }
    )

    try:
        response = client.post(
            "/ingest/pdf",
            data={"title": "Sample PDF"},
            files={"file": ("sample.pdf", MINIMAL_PDF, "application/pdf")},
        )
        assert response.status_code == 200
        body = response.json()
    finally:
        client.close()

    assert body["pages_ingested"] >= 1
    assert body["chunks_ingested"] >= 1
