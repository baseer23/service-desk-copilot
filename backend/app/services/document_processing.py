"""Optional Azure Document Intelligence processing; originals remain local."""

import base64
import hashlib
import logging
import traceback
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from azure.core.pipeline.policies import RetryPolicy

from backend.app.core.config import Settings
from backend.app.services.form_marks import readable_marks
from backend.app.core.azure_identity import azure_credential
from backend.app.store.records import ManagedStoreError

logger = logging.getLogger(__name__)


class ThrottleOnlyRetryPolicy(RetryPolicy):
    """Retry only HTTP 429 (rejected before analysis/billing), honouring Retry-After up to 60s."""

    def is_retry(self, settings, response):
        if response.http_response.status_code != 429 or settings["total"] <= 0:
            return False
        wait = self.get_retry_after(response)
        return wait is None or wait <= 60


class DocumentProcessingFailure(ManagedStoreError):
    """Sanitized external-processing failure safe for durable document state."""

    def __init__(self, code: str, reason: str, source_exception_type: str, safe_traceback: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.source_exception_type = source_exception_type
        self.safe_traceback = safe_traceback


def _safe_traceback(exc: Exception) -> str:
    return " <- ".join(
        f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
        for frame in traceback.extract_tb(exc.__traceback__)[-8:]
    )


def _azure_failure(exc: Exception) -> DocumentProcessingFailure:
    kind = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if kind in {"ClientAuthenticationError", "CredentialUnavailableError"}:
        code, reason = "AZURE_DOCUMENT_AUTHENTICATION_FAILED", "Azure Document Intelligence authentication failed."
    elif status in {401, 403}:
        code, reason = "AZURE_DOCUMENT_AUTHORIZATION_FAILED", "Azure Document Intelligence rejected the configured identity."
    elif status == 429:
        code, reason = "AZURE_DOCUMENT_CAPACITY", "Azure Document Intelligence is temporarily at capacity."
    elif kind == "ServiceResponseTimeoutError":
        # The request may already have been accepted (and billed): never retried
        # automatically; an operator reprocess resumes from the saved batches.
        code, reason = (
            "AZURE_DOCUMENT_TIMEOUT",
            "Azure Document Intelligence did not respond in time; the request may still have "
            "been processed.",
        )
    elif kind == "ServiceRequestError":
        # The request never reached Azure.
        code, reason = "AZURE_DOCUMENT_UNREACHABLE", "Azure Document Intelligence could not be reached."
    elif kind == "ServiceResponseError":
        # Sent, but the response was lost: ambiguous, like a timeout.
        code, reason = (
            "AZURE_DOCUMENT_RESPONSE_LOST",
            "Azure Document Intelligence's response was lost; the request may still have been "
            "processed.",
        )
    elif status == 400:
        code, reason = "AZURE_DOCUMENT_REJECTED", "Azure Document Intelligence rejected this document."
    else:
        code, reason = "AZURE_DOCUMENT_PROCESSING_FAILED", "Azure Document Intelligence could not process this document."
    return DocumentProcessingFailure(code, reason, kind, _safe_traceback(exc))


def azure_document_readiness(settings: Settings) -> dict[str, object]:
    """Perform a read-only authenticated resource probe without analyzing content."""
    if not settings.azure_document_endpoint:
        return {
            "available": False,
            "code": "AZURE_DOCUMENT_ENDPOINT_MISSING",
            "blocker": "AZURE_DOCUMENT_ENDPOINT is required.",
        }
    client = credential = None
    try:
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.rest import HttpRequest

        credential = azure_credential(settings)
        client = DocumentIntelligenceClient(
            settings.azure_document_endpoint,
            credential,
            api_version=settings.azure_document_api_version,
            retry_total=0,
            connection_timeout=5,
            read_timeout=10,
        )
        endpoint = settings.azure_document_endpoint.rstrip("/")
        response = client.send_request(HttpRequest(
            "GET",
            f"{endpoint}/documentintelligence/info?api-version={settings.azure_document_api_version}",
        ))
        if response.status_code >= 400:
            response.raise_for_status()
        return {"available": True, "code": None, "blocker": None}
    except Exception as exc:
        failure = _azure_failure(exc)
        logger.warning(
            "Azure Document Intelligence readiness failed type=%s code=%s traceback=%s",
            failure.source_exception_type, failure.code, failure.safe_traceback,
        )
        return {"available": False, "code": failure.code, "blocker": failure.reason}
    finally:
        if client:
            client.close()
        if credential:
            credential.close()


class AzureDocumentExtractor:
    def __init__(self, settings: Settings, records, client: Any = None):
        self.settings, self.records = settings, records
        self.credential = None
        if client is None:
            from azure.ai.documentintelligence import DocumentIntelligenceClient

            self.credential = azure_credential(settings)
            client = DocumentIntelligenceClient(
                settings.azure_document_endpoint,
                self.credential,
                api_version=settings.azure_document_api_version,
                retry_policy=ThrottleOnlyRetryPolicy(
                    retry_total=4, retry_status=4, retry_connect=0, retry_read=0,
                    retry_backoff_factor=2, retry_backoff_max=60,
                ),
                connection_timeout=5,
                # The analyze POST carries the whole segment (up to 4 MB on the free
                # tier) and can take well over 15 s to be accepted under load.
                read_timeout=60,
            )
        self.client = client

    def _failure(self, exc):
        failure = _azure_failure(exc)
        logger.error(
            "Azure document extraction failed type=%s code=%s traceback=%s",
            failure.source_exception_type, failure.code, failure.safe_traceback,
        )
        return failure

    def _analysis_result(self, poller):
        """Wait for an accepted analysis; a failed status poll resumes the same operation.

        Polling is a free GET of an operation Azure already accepted, so it is
        safe to repeat; the billable analyze POST is never re-sent here.
        """
        from azure.core.exceptions import ServiceRequestError, ServiceResponseError

        try:
            token = poller.continuation_token()
        except Exception:  # noqa: BLE001 - a poller without a token cannot be resumed
            token = None
        for attempt in range(3):
            try:
                return poller.result(timeout=600)
            except (ServiceRequestError, ServiceResponseError) as exc:
                if token is None or attempt == 2:
                    raise self._failure(exc) from None
                logger.warning(
                    "Azure analysis status poll failed type=%s; resuming the same operation",
                    type(exc).__name__,
                )
                poller = self.client.begin_analyze_document(
                    "prebuilt-layout", None, continuation_token=token
                )
            except Exception as exc:  # noqa: BLE001 - mapped to a sanitized failure
                raise self._failure(exc) from None

    def extract(self, data: bytes, media_type: str, enhanced: bool = False):
        segments = [(data, 0)]
        if (
            media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            and self.settings.azure_document_free_tier
        ):
            from backend.app.services.azure_document_formats import word_segments

            segments = word_segments(data)
        if media_type == "image/tiff":
            from PIL import Image

            with Image.open(BytesIO(data)) as image:
                if image.n_frames > 2 and self.settings.azure_document_free_tier:
                    raise ValueError("Convert multi-page TIFF files to PDF to process every page")
        if media_type == "application/pdf":
            from pypdf import PdfReader, PdfWriter

            reader = PdfReader(BytesIO(data))
            if reader.is_encrypted:
                raise ValueError("Encrypted PDFs are not supported")
            segments = []
            for start in range(
                0, len(reader.pages), 2 if self.settings.azure_document_free_tier else 100
            ):
                writer = PdfWriter()
                for page in reader.pages[
                    start : start + (2 if self.settings.azure_document_free_tier else 100)
                ]:
                    writer.add_page(page)
                output = BytesIO()
                writer.write(output)
                segments.append((output.getvalue(), start))
        pages, tables, paragraphs, figures = [], [], [], []
        for segment, offset in segments:
            request_limit = 4_000_000 if self.settings.azure_document_free_tier else 500_000_000
            if len(segment) > request_limit:
                raise ValueError("A single document page exceeds the Azure request-size limit")
            try:
                arguments = {
                    "body": BytesIO(segment),
                    # Layout v4.0 analyses HTML as text (billed per 3,000 characters).
                    "content_type": (
                        "text/html" if media_type == "text/html" else "application/octet-stream"
                    ),
                    "output_content_format": "markdown",
                }
                if enhanced and media_type != "text/html":
                    try:
                        from azure.ai.documentintelligence.models import AnalyzeOutputOption

                        arguments["output"] = [AnalyzeOutputOption.FIGURES]
                    except (ImportError, AttributeError):
                        pass
                try:
                    poller = self.client.begin_analyze_document("prebuilt-layout", **arguments)
                except Exception as exc:  # noqa: BLE001 - the submit itself failed
                    raise self._failure(exc) from None
                result = self._analysis_result(poller)
                for page in result.pages or []:
                    page_number = page.page_number + offset
                    # Azure's own Markdown for the page (tables, headings and ☒/☐
                    # marks), sliced from the result content by the page's spans.
                    markdown = "".join(
                        result.content[span.offset : span.offset + span.length]
                        for span in page.spans or []
                    ) if result.content else ""
                    text = "\n".join(line.content for line in page.lines or []) or markdown
                    pages.append(
                        {
                            "page": page_number,
                            "text": text,
                            "markdown": readable_marks(markdown or text),
                            "spans": [
                                {"offset": span.offset, "length": span.length}
                                for span in page.spans or []
                            ],
                            "selection_marks": [
                                _selection_mark(mark, page_number, ordinal, offset)
                                for ordinal, mark in enumerate(
                                    getattr(page, "selection_marks", None) or []
                                )
                            ],
                        }
                    )
                for ordinal, paragraph in enumerate(getattr(result, "paragraphs", None) or []):
                    regions = getattr(paragraph, "bounding_regions", None) or []
                    page_number = (regions[0].page_number if regions else 1) + offset
                    paragraphs.append(
                        {
                            "id": f"paragraph-{page_number}-{ordinal}",
                            "page": page_number,
                            "text": paragraph.content,
                            "role": getattr(paragraph, "role", None),
                            "spans": [
                                {"offset": span.offset, "length": span.length}
                                for span in getattr(paragraph, "spans", None) or []
                            ],
                            "position": ordinal,
                            "bounding_regions": _regions(regions, offset),
                        }
                    )
                for table in result.tables or []:
                    cells = [
                        {
                            "row": cell.row_index,
                            "column": cell.column_index,
                            "row_span": getattr(cell, "row_span", 1) or 1,
                            "column_span": getattr(cell, "column_span", 1) or 1,
                            # The enum's value ("columnHeader"), not its Python name.
                            "kind": str(
                                getattr(getattr(cell, "kind", None), "value", None)
                                or getattr(cell, "kind", None) or "content"
                            ),
                            "text": cell.content,
                            "spans": [
                                {"offset": span.offset, "length": span.length}
                                for span in getattr(cell, "spans", None) or []
                            ],
                            "bounding_regions": _regions(
                                getattr(cell, "bounding_regions", None) or [], offset
                            ),
                        }
                        for cell in table.cells
                    ]
                    table_page = (
                        table.bounding_regions[0].page_number if table.bounding_regions else 1
                    ) + offset
                    tables.append(
                        {
                            "id": f"table-{table_page}-{len(tables)}",
                            "page": table_page,
                            "row_count": getattr(table, "row_count", 0),
                            "column_count": getattr(table, "column_count", 0),
                            "caption": getattr(
                                getattr(table, "caption", None), "content", None
                            ),
                            "cells": cells,
                            "bounding_regions": _regions(table.bounding_regions or [], offset),
                        }
                    )
                for ordinal, figure in enumerate(getattr(result, "figures", None) or []):
                    regions = getattr(figure, "bounding_regions", None) or []
                    page_number = (regions[0].page_number if regions else 1) + offset
                    figure_id = str(
                        getattr(figure, "id", None) or f"figure-{page_number}-{ordinal}"
                    )
                    caption = getattr(getattr(figure, "caption", None), "content", None)
                    record = {
                        "id": figure_id,
                        "page": page_number,
                        "caption": caption,
                        "related_text": [],
                        "position": ordinal,
                        "bounding_regions": _regions(regions, offset),
                        "image_status": "unsupported",
                    }
                    if enhanced and getattr(poller, "details", None):
                        operation_id = poller.details.get("operation_id")
                        if operation_id and hasattr(self.client, "get_analyze_result_figure"):
                            try:
                                content = self.client.get_analyze_result_figure(
                                    "prebuilt-layout", operation_id, figure_id
                                )
                                image = (
                                    b"".join(content) if not isinstance(content, bytes) else content
                                )
                                if image:
                                    record["image_base64"] = base64.b64encode(image).decode()
                                    record["image_status"] = "available"
                            except Exception:  # Figure crops are optional; structure is retained.
                                record["image_status"] = "unavailable"
                    figures.append(record)
                if not result.pages and result.content:
                    pages.append({"page": offset + 1, "text": result.content})
            except DocumentProcessingFailure:
                raise
            except Exception as exc:  # noqa: BLE001 - our parsing of a successful result
                # Not an Azure failure: report the real exception, not "Azure could not".
                trace = _safe_traceback(exc)
                logger.error("Document parsing failed type=%s traceback=%s",
                             type(exc).__name__, trace)
                raise DocumentProcessingFailure(
                    "DOCUMENT_PARSING_FAILED",
                    "DeskMate could not read Azure Document Intelligence's result for this "
                    "document.", type(exc).__name__, trace,
                ) from None
        if not any(page["text"].strip() for page in pages):
            raise ValueError("The document contains no readable text")
        return {
            "pages": pages,
            "tables": tables,
            "paragraphs": paragraphs,
            "sections": _sections(paragraphs),
            "figures": figures,
            "method": "document_intelligence",
            "metadata": {
                "format": media_type,
                "page_count": len(pages),
                "table_count": len(tables),
                "classification": "table_document" if tables else "text_document",
                "classification_method": "layout_rules",
                "structured": enhanced,
                "image_understanding": "not_performed",
            },
        }

    def extract_path(
        self, path, media_type: str, enhanced: bool = False,
        load_batch=lambda _index: None, save_batch=lambda _index, _result: None,
    ):
        path = Path(path)
        if media_type != "application/pdf":
            return self.extract(path.read_bytes(), media_type, enhanced)
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(str(path))
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported")
        if not reader.pages:
            raise ValueError("The PDF contains no pages")
        page_limit = 2 if self.settings.azure_document_free_tier else 100
        byte_limit = 4_000_000 if self.settings.azure_document_free_tier else 500_000_000
        ranges = [(start, min(start + page_limit, len(reader.pages)))
                  for start in range(0, len(reader.pages), page_limit)]
        results = []
        raster_results = {}
        batch_index = 0
        while ranges:
            start, end = ranges.pop(0)
            writer = PdfWriter()
            for page in reader.pages[start:end]:
                writer.add_page(page)
            output = BytesIO()
            writer.write(output)
            segment = output.getvalue()
            if len(segment) > byte_limit and end - start > 1:
                middle = start + (end - start) // 2
                ranges[0:0] = [(start, middle), (middle, end)]
                continue
            request_media_type = media_type
            source_image_hash = None
            if len(segment) > byte_limit:
                segment = _compressed_page_image(reader.pages[start], byte_limit)
                request_media_type = "image/jpeg"
                source_image_hash = hashlib.sha256(segment).hexdigest()
            batch = load_batch(batch_index)
            if batch is None:
                if source_image_hash and source_image_hash in raster_results:
                    batch = {
                        "pages": [], "paragraphs": [], "sections": [], "tables": [],
                        "figures": [], "method": "document_intelligence",
                        "metadata": {"duplicate_raster_of": raster_results[source_image_hash],
                                     "source_image_hash": source_image_hash},
                    }
                else:
                    batch = self.extract(segment, request_media_type, enhanced)
                    batch = _offset_batch(batch, start, batch_index)
                    if source_image_hash:
                        batch.setdefault("metadata", {})["source_image_hash"] = source_image_hash
                        raster_results[source_image_hash] = batch_index
                save_batch(batch_index, batch)
            elif batch.get("metadata", {}).get("source_image_hash"):
                raster_results[batch["metadata"]["source_image_hash"]] = batch_index
            results.append(batch)
            batch_index += 1
        pages = [item for result in results for item in result.get("pages", [])]
        paragraphs = [item for result in results for item in result.get("paragraphs", [])]
        tables = [item for result in results for item in result.get("tables", [])]
        figures = [item for result in results for item in result.get("figures", [])]
        return {
            "pages": pages, "paragraphs": paragraphs, "sections": _sections(paragraphs),
            "tables": tables, "figures": figures, "method": "document_intelligence",
            "metadata": {
                "format": media_type, "page_count": len(pages), "table_count": len(tables),
                "classification": "table_document" if tables else "text_document",
                "classification_method": "layout_rules", "structured": enhanced,
                "image_understanding": "not_performed", "completed_batches": len(results),
            },
        }


def _sections(paragraphs):
    """Build a deterministic heading hierarchy without inventing semantics."""
    sections, stack = [], []
    for paragraph in paragraphs:
        role = str(paragraph.get("role") or "")
        if role not in {"title", "sectionHeading"}:
            continue
        level = 1 if role == "title" else 2
        stack = stack[: level - 1]
        section = {
            "id": f"section-{paragraph['page']}-{paragraph['position']}",
            "page": paragraph["page"],
            "title": paragraph["text"],
            "level": level,
            "parent_id": stack[-1] if stack else None,
        }
        sections.append(section)
        stack.append(section["id"])
    return sections


def _selection_mark(mark, page_number, ordinal, offset=0):
    """A saved selection mark: state, confidence, polygon and span, with a stable ID."""
    span = getattr(mark, "span", None)
    return {
        "id": f"selectionmark-{page_number}-{ordinal}",
        "page": page_number,
        "state": str(getattr(mark, "state", "") or "").lower(),
        "confidence": getattr(mark, "confidence", None),
        "bounding_regions": _regions(
            [SimpleNamespace(
                page_number=page_number - offset, polygon=getattr(mark, "polygon", None)
            )],
            offset,
        ),
        "span": {"offset": span.offset, "length": span.length} if span else None,
    }


def with_heading_sections(extraction):
    """Derive sections a saved extraction's heading paragraphs define but it lacks."""
    if extraction.get("sections") or not extraction.get("paragraphs"):
        return extraction
    return {**extraction, "sections": _sections(extraction["paragraphs"])}


def _offset_batch(batch, offset, batch_index):
    for page in batch.get("pages", []):
        # Marks are numbered per document page, so their IDs stay stable.
        for ordinal, mark in enumerate(page.get("selection_marks") or []):
            mark["page"] = mark.get("page", 1) + offset
            mark["id"] = f"selectionmark-{mark['page']}-{ordinal}"
            for region in mark.get("bounding_regions", []):
                region["page"] = region.get("page", 1) + offset
    for collection in ("pages", "paragraphs", "tables", "figures"):
        for ordinal, item in enumerate(batch.get(collection, [])):
            item["page"] = item.get("page", 1) + offset
            if collection in {"paragraphs", "tables", "figures"}:
                item["id"] = f"batch-{batch_index}-{collection}-{ordinal}"
            for region in item.get("bounding_regions", []):
                region["page"] = region.get("page", 1) + offset
    batch["sections"] = []
    return batch


def _compressed_page_image(page, byte_limit):
    """Use the dominant embedded screenshot when a PDF page cannot fit one request."""
    images = [item.image for item in page.images]
    if not images:
        raise ValueError("A single PDF page exceeds the Azure request-size limit")
    image = max(images, key=lambda item: item.width * item.height).convert("RGB")
    while True:
        for quality in (85, 75, 65, 55, 45, 35):
            output = BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            if output.tell() <= byte_limit:
                return output.getvalue()
        if image.width <= 1200 or image.height <= 1200:
            raise ValueError("A single page image exceeds the Azure request-size limit")
        image.thumbnail((int(image.width * 0.8), int(image.height * 0.8)))


def _regions(regions, offset=0):
    def polygon_points(polygon):
        values = list(polygon or [])
        if values and all(isinstance(value, (int, float)) for value in values):
            return [
                {"x": float(values[index]), "y": float(values[index + 1])}
                for index in range(0, len(values) - 1, 2)
            ]
        points = []
        for point in values:
            if isinstance(point, dict) and "x" in point and "y" in point:
                points.append({"x": float(point["x"]), "y": float(point["y"])})
            elif hasattr(point, "x") and hasattr(point, "y"):
                points.append({"x": float(point.x), "y": float(point.y)})
        return points

    return [
        {
            "page": region.page_number + offset,
            "polygon": polygon_points(getattr(region, "polygon", None)),
        }
        for region in regions
    ]
