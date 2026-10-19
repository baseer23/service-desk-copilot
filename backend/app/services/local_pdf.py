"""Native per-page PDF text in a bounded subprocess; no network or OCR dispatch."""

import json
import subprocess
import sys
from io import BytesIO


def extract_pdf_pages(data):
    if not data:
        raise ValueError("Choose a nonempty PDF")
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


def extract_pdf_pages_path(path):
    try:
        process = subprocess.run(
            [sys.executable, "-m", "backend.app.services.local_pdf", str(path)],
            capture_output=True, timeout=300, check=False,
        )
        result = json.loads(process.stdout)
    except subprocess.TimeoutExpired:
        raise ValueError("Document extraction exceeded the local processing timeout") from None
    except (OSError, ValueError):
        raise ValueError("PDF could not be parsed") from None
    if process.returncode or result.get("error"):
        raise ValueError(result.get("error", "PDF could not be parsed"))
    return result["pages"]


def _extract(source):
    from pypdf import PdfReader

    reader = PdfReader(source)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDFs are not supported")
    if not reader.pages:
        raise ValueError("The PDF contains no pages")
    pages = []
    for index, page in enumerate(reader.pages):
        text = page.extract_text() or ""
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
        source = sys.argv[1] if len(sys.argv) > 1 else BytesIO(sys.stdin.buffer.read())
        print(json.dumps({"pages": _extract(source)}))
    except Exception:
        print(json.dumps({"error": "PDF could not be parsed within document limits"}))
        sys.exit(1)
