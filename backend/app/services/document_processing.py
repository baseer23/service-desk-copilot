"""Optional Azure Document Intelligence processing; originals remain local."""

import time
from io import BytesIO
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

    def extract(self, data: bytes, media_type: str):
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
            if reader.is_encrypted or len(reader.pages) > 100:
                raise ValueError("Choose an unencrypted document of at most 100 pages")
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
        pages, tables = [], []
        deadline = time.monotonic() + 90
        for segment, offset in segments:
            if len(segment) > 4_000_000 or time.monotonic() >= deadline:
                raise ValueError("Document processing limit reached; use smaller document sections")
            try:
                result = self.client.begin_analyze_document(
                    "prebuilt-layout",
                    body=BytesIO(segment),
                    content_type="application/octet-stream",
                    output_content_format="markdown",
                ).result(timeout=min(30, max(1, deadline - time.monotonic())))
                for page in result.pages or []:
                    text = "\n".join(line.content for line in page.lines or [])
                    if not text and result.content:
                        text = "\n".join(
                            result.content[span.offset : span.offset + span.length]
                            for span in page.spans or []
                        )
                    pages.append({"page": page.page_number + offset, "text": text})
                for table in result.tables or []:
                    cells = [
                        {"row": cell.row_index, "column": cell.column_index, "text": cell.content}
                        for cell in table.cells
                    ]
                    tables.append(
                        {
                            "page": (
                                table.bounding_regions[0].page_number
                                if table.bounding_regions
                                else 1
                            )
                            + offset,
                            "cells": cells,
                        }
                    )
                if not result.pages and result.content:
                    pages.append({"page": offset + 1, "text": result.content})
            except Exception:  # noqa: BLE001 - Sanitize SDK/auth failures without disclosing response bodies.
                raise ManagedStoreError("Document text and layout could not be processed") from None
        if not any(page["text"].strip() for page in pages):
            raise ValueError("The document contains no readable text")
        return {
            "pages": pages,
            "tables": tables,
            "method": "document_intelligence",
            "metadata": {
                "format": media_type,
                "page_count": len(pages),
                "table_count": len(tables),
                "classification": "table_document" if tables else "text_document",
                "classification_method": "layout_rules",
            },
        }
