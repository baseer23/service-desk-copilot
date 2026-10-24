"""Bounded local document extraction. No Azure clients, storage or credentials."""

import json
import shutil
import subprocess
import sys
import tempfile
from io import BytesIO
from pathlib import Path

MAX_TEXT = 1_000_000


class DocumentError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def ocr_available() -> bool:
    return bool(shutil.which("pdftoppm") and shutil.which("tesseract"))


def extract_document(data: bytes, media_type: str) -> dict:
    if media_type in {"text/plain", "text/markdown"}:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise DocumentError("Text documents must use UTF-8") from None
        if not text.strip():
            raise DocumentError("Document contains no text")
        if len(text) > MAX_TEXT:
            raise DocumentError("Extracted text exceeds one million characters", 413)
        return {"text": text, "pages": 1, "method": "text", "ocr_available": ocr_available()}
    if media_type != "application/pdf" or not data.startswith(b"%PDF-"):
        raise DocumentError("Choose a valid PDF or UTF-8 text document", 400)
    # Isolate PDF parsing/OCR so timeout kills the actual work, rather than only
    # cancelling an await while a parsing thread keeps running indefinitely.
    try:
        process = subprocess.run(  # noqa: S603 - fixed module, bytes on stdin, no shell
            [sys.executable, "-m", "backend.app.services.document_extract"],
            input=data,
            capture_output=True,
            timeout=35,
            check=False,
        )
        result = json.loads(process.stdout)
    except subprocess.TimeoutExpired:
        raise DocumentError("Document extraction timed out; use a smaller PDF") from None
    except (OSError, ValueError):
        raise DocumentError("PDF could not be parsed") from None
    if process.returncode or "error" in result:
        raise DocumentError(result.get("error", "PDF could not be parsed"))
    return result


def extract_document_path(path: Path, media_type: str) -> dict:
    if media_type in {"text/plain", "text/markdown"}:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            raise DocumentError("Text documents must use UTF-8") from None
        if not text.strip():
            raise DocumentError("Document contains no text")
        return {"text": text, "pages": 1, "method": "text", "ocr_available": ocr_available()}
    if media_type != "application/pdf":
        raise DocumentError("Preview is available for PDF or UTF-8 text documents", 400)
    from backend.app.services.local_pdf import extract_pdf_pages_path

    try:
        pages = extract_pdf_pages_path(path)
    except DocumentError:
        raise
    except Exception:
        raise DocumentError("PDF could not be parsed") from None
    return {
        "text": "\n\n".join(page["text"] for page in pages),
        "pages": len(pages), "method": "pdf_text", "ocr_available": ocr_available(),
    }


def _pdf_worker(data: bytes) -> dict:
    from pdfminer.high_level import extract_text
    from pdfminer.pdfpage import PDFPage

    pages = 0
    for _ in PDFPage.get_pages(BytesIO(data), check_extractable=True):
        pages += 1
    if not pages:
        raise DocumentError("PDF contains no pages")
    text = extract_text(BytesIO(data))
    method = "pdf_text"
    if not text.strip():
        renderer, recognizer = shutil.which("pdftoppm"), shutil.which("tesseract")
        if not ocr_available() or renderer is None or recognizer is None:
            raise DocumentError(
                "Scanned PDF has no extractable text. Local OCR requires pdftoppm "
                "(Poppler) and Tesseract; upload a text-based PDF or paste its text."
            )
        with tempfile.TemporaryDirectory(prefix="deskmate-ocr-") as directory:
            root = Path(directory)
            (root / "input.pdf").write_bytes(data)
            subprocess.run(  # noqa: S603 - installed executable, generated private paths
                [
                    renderer,
                    "-r",
                    "100",
                    "-scale-to",
                    "1600",
                    "-png",
                    str(root / "input.pdf"),
                    str(root / "page"),
                ],
                capture_output=True,
                timeout=10,
                check=True,
            )
            results = []
            for path in sorted(root.glob("page-*.png")):
                result = subprocess.run(  # noqa: S603 - installed executable, generated paths
                    [recognizer, str(path), "stdout"],
                    capture_output=True,
                    timeout=10,
                    check=True,
                )
                results.append(result.stdout.decode("utf-8"))
            text = "\f".join(results)
            method = "local_ocr"
    if not text.strip():
        raise DocumentError("Document contains no readable text")
    if len(text) > MAX_TEXT:
        raise DocumentError("Extracted text exceeds one million characters")
    return {"text": text, "pages": pages, "method": method, "ocr_available": ocr_available()}


if __name__ == "__main__":
    try:
        print(json.dumps(_pdf_worker(sys.stdin.buffer.read())))
    except DocumentError as error:
        print(json.dumps({"error": str(error)}))
        sys.exit(1)
    except Exception:  # noqa: BLE001 - sanitize the untrusted PDF parser boundary
        print(json.dumps({"error": "PDF could not be parsed or local OCR failed"}))
        sys.exit(1)
