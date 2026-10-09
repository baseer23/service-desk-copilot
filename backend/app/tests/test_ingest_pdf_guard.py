"""PDF ingest should succeed when pdfminer is available."""

from __future__ import annotations



try:  # pragma: no cover - exercised conditionally
    from pdfminer.high_level import extract_text  # noqa: F401 - import check
except ImportError:  # pragma: no cover
    extract_text = None


def minimal_pdf():
    stream = b"BT /F1 24 Tf 72 100 Td (Hello PDF) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Count 1 /Kids [3 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n")
    start = len(data)
    data.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF".encode())
    return bytes(data)


MINIMAL_PDF = minimal_pdf()


def test_ingest_pdf_roundtrip(make_client, tmp_path):
    client = make_client(
        {
            "GENERATION_PROVIDER": "stub",
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
