"""Optional Azure Document Intelligence processing; originals remain local."""

import base64
import hashlib
from io import BytesIO
from pathlib import Path
from typing import Any

from backend.app.core.config import Settings
from backend.app.core.azure_identity import azure_credential
from backend.app.store.records import ManagedStoreError


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
                retry_total=0,
                connection_timeout=5,
                read_timeout=15,
            )
        self.client = client

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
                    "content_type": "application/octet-stream",
                    "output_content_format": "markdown",
                }
                if enhanced:
                    try:
                        from azure.ai.documentintelligence.models import AnalyzeOutputOption

                        arguments["output"] = [AnalyzeOutputOption.FIGURES]
                    except (ImportError, AttributeError):
                        pass
                poller = self.client.begin_analyze_document("prebuilt-layout", **arguments)
                result = poller.result(timeout=600)
                for page in result.pages or []:
                    text = "\n".join(line.content for line in page.lines or [])
                    if not text and result.content:
                        text = "\n".join(
                            result.content[span.offset : span.offset + span.length]
                            for span in page.spans or []
                        )
                    pages.append(
                        {
                            "page": page.page_number + offset,
                            "text": text,
                            "markdown": text,
                            "spans": [
                                {"offset": span.offset, "length": span.length}
                                for span in page.spans or []
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
                            "kind": str(getattr(cell, "kind", "content") or "content"),
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
            except Exception:  # noqa: BLE001 - Sanitize SDK/auth failures without disclosing response bodies.
                raise ManagedStoreError("Document text and layout could not be processed") from None
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


def _offset_batch(batch, offset, batch_index):
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
    return [
        {
            "page": region.page_number + offset,
            "polygon": [
                {"x": point.x, "y": point.y} for point in getattr(region, "polygon", None) or []
            ],
        }
        for region in regions
    ]
