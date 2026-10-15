"""Native per-page PDF text in a bounded subprocess; no network or OCR dispatch."""

import json
import subprocess
import sys
from io import BytesIO


def extract_pdf_pages(data):
    if not data or len(data) > 5_000_000:
        raise ValueError("Choose a PDF up to 5 MB")
    try:
        process = subprocess.run(
            [sys.executable, "-m", "backend.app.services.local_pdf"],
            input=data, capture_output=True, timeout=35, check=False,
        )
        result = json.loads(process.stdout)
    except subprocess.TimeoutExpired:
        raise ValueError("Document extraction timed out; use a smaller PDF") from None
    except (OSError, ValueError):
        raise ValueError("PDF could not be parsed") from None
    if process.returncode or result.get("error"):
        raise ValueError(result.get("error", "PDF could not be parsed"))
    return result["pages"]


def _extract(data):
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted or len(reader.pages) > 100:
        raise ValueError("Choose an unencrypted document of at most 100 pages")
    if not reader.pages:
        raise ValueError("The PDF contains no pages")
    pages, characters = [], 0
    for index, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        characters += len(text)
        if characters > 1_000_000:
            raise ValueError("Extracted text exceeds one million characters")
        pages.append({"page": index + 1, "text": text})
    return pages


if __name__ == "__main__":
    try:
        # This worker imports no model or application settings. Bound parser
        # allocation where supported, in addition to the parent wall timeout.
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_DATA, (512 * 1024 * 1024, 512 * 1024 * 1024))
        except (ImportError, OSError, ValueError):
            pass
        print(json.dumps({"pages": _extract(sys.stdin.buffer.read(5_000_001))}))
    except Exception:
        print(json.dumps({"error": "PDF could not be parsed within document limits"}))
        sys.exit(1)
