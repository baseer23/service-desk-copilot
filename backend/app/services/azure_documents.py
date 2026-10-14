"""Durable, staged Blob → Document Intelligence → embeddings → Search ingestion."""

import hashlib
import json
import time
from io import BytesIO
from typing import Any

from backend.app.adapters.embeddings import get_embedding_provider
from backend.app.core.azure_usage import AzureUsageBlocked, AzureUsageGuard
from backend.app.core.config import Settings
from backend.app.services.chunking import split_text
from backend.app.store.azure_knowledge import AzureBlobDocuments, AzureSearchStore
from backend.app.store.azure_records import CosmosRecords, ManagedStoreError, azure_credential


class AzureDocumentExtractor:
    def __init__(self, settings: Settings, records: CosmosRecords, client: Any = None):
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
        AzureUsageGuard(self.settings).require_enabled()
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
            AzureUsageGuard(self.settings).require_enabled()
            if len(segment) > 4_000_000 or time.monotonic() >= deadline:
                raise ValueError("Document processing limit reached; use smaller document sections")
            with self.records.locked(
                "document_quota", time.strftime("%Y-%m", time.gmtime()), tenant="system:documents"
            ) as quota:
                charged = 2 if self.settings.azure_document_free_tier else 100
                if (
                    quota.get("pages", 0) + charged
                    > self.settings.azure_document_monthly_page_limit
                ):
                    raise ManagedStoreError("Document processing allowance is full")
                quota["pages"] = quota.get("pages", 0) + charged
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


class AzureDocumentRepository:
    def __init__(
        self,
        settings: Settings,
        records=None,
        blobs=None,
        search=None,
        extractor=None,
        embeddings=None,
    ):
        self.settings = settings
        self.records = records or CosmosRecords(settings)
        self.blobs = blobs or AzureBlobDocuments(settings, records=self.records)
        self.search_store = search or AzureSearchStore(settings, records=self.records)
        self.extractor = extractor or AzureDocumentExtractor(settings, self.records)
        self.embeddings = embeddings or get_embedding_provider(settings)

    def upload(self, data: bytes, title: str | None, media_type: str):
        if not data or len(data) > 5_000_000 or len(title or "") > 300:
            raise ValueError(
                "Choose a nonempty document up to 5 MB and a title up to 300 characters"
            )
        if media_type in {"text/plain", "text/markdown"} and not data.decode("utf-8-sig").strip():
            raise ValueError("The document contains no readable text")
        identifier = hashlib.sha256(data).hexdigest()
        previous = self.records.read("document", identifier)
        if previous:
            return {
                "id": identifier,
                "duplicate": True,
                "chunks": previous["payload"].get("chunks", 0),
                "status": previous["payload"]["status"],
                "storage": "azure",
                "indexed": previous["payload"]["status"] == "ready",
            }
        self.blobs.put(identifier, "original", data, media_type)
        payload = {
            "id": identifier,
            "title": title or "Untitled",
            "source_type": "pdf" if media_type == "application/pdf" else "document",
            "media_type": media_type,
            "status": "queued",
            "phase": "extract",
            "chunks": 0,
            "pages": 0,
        }
        family = hashlib.sha256((title or identifier).strip().casefold().encode()).hexdigest()
        with self.records.locked("document_family", family) as history:
            versions = history.setdefault("versions", [])
            payload.update(family=family, version=len(versions) + 1, content_hash=identifier)
            self.records.write(
                "document", identifier, payload, create_only=True, fields={"status": "queued"}
            )
            versions.append(identifier)
        return {**payload, "duplicate": False, "storage": "azure", "indexed": False}

    def save(self, text: str, title: str | None, source_type: str, pages: int = 1):
        return self.upload(text.encode(), title, "text/plain")

    def list_documents(self):
        return [row["payload"] for row in self.records.query("document", limit=100)]

    def get(self, identifier: str):
        row = self.records.read("document", identifier)
        if not row:
            raise ValueError("Document not found")
        return row["payload"]

    def search(self, question: str, top_k: int):
        return self.search_store.search_keywords(question, top_k)

    def process(self, identifier: str):
        row = self.records.read("document", identifier)
        if not row:
            raise ValueError("Document not found")
        state = dict(row["payload"])
        if state["status"] == "ready":
            return state
        # Never automatically repeat a paid operation whose outcome was not saved.
        if state["phase"] in {"extract_inflight", "embedding_inflight"}:
            raise ManagedStoreError("This document needs an operator review before retrying")
        try:
            if state["phase"] == "extract":
                original = self.blobs.get(identifier, "original")
                if state["media_type"] in {"text/plain", "text/markdown"}:
                    result = {
                        "pages": [{"page": 1, "text": original.decode("utf-8-sig")}],
                        "tables": [],
                        "method": "native_text",
                    }
                else:
                    AzureUsageGuard(self.settings).require_enabled()
                    state.update(phase="extract_inflight", status="processing")
                    row = self.records.write("document", identifier, state, previous=row)
                    result = self.extractor.extract(original, state["media_type"])
                self.blobs.put(
                    identifier, "extraction.json", json.dumps(result).encode(), "application/json"
                )
                state.update(phase="embed", status="processing", pages=len(result["pages"]))
                row = self.records.write(
                    "document", identifier, state, previous=row, fields={"status": "processing"}
                )
            result = json.loads(self.blobs.get(identifier, "extraction.json"))
            chunks = []
            ordinal = 0
            for page in result["pages"]:
                text = page["text"]
                matching_tables = [
                    table for table in result["tables"] if table["page"] == page["page"]
                ]
                if matching_tables:
                    text += "\n" + "\n".join(
                        " | ".join(cell["text"] for cell in table["cells"])
                        for table in matching_tables
                    )
                for chunk in split_text(
                    text, self.settings.chunk_tokens, self.settings.chunk_overlap
                ):
                    chunks.append(
                        {
                            "id": identifier + "-" + str(ordinal),
                            "text": chunk["text"],
                            "embedding": None,
                            "metadata": {
                                "doc_id": identifier,
                                "title": state["title"],
                                "source_type": state["source_type"],
                                "page": page["page"],
                                "source": "/documents/" + identifier + "/download",
                                "content_hash": identifier,
                                "version": state.get("version", 1),
                                "family": state.get("family"),
                                "embedding_provider": "azure_openai",
                                "embedding_model": self.settings.embed_model_name,
                            },
                        }
                    )
                    ordinal += 1
            if not chunks:
                raise ValueError("The document contains no readable text")
            # Lexical publication uses actual extracted text, never placeholder vectors.
            self.search_store.upsert(chunks)
            if state.get("family"):
                with self.records.locked("document_family", state["family"]) as history:
                    if state.get("version", 1) >= history.get("published_version", 0):
                        history.update(
                            published=identifier, published_version=state.get("version", 1)
                        )
            if state["phase"] == "embed":
                AzureUsageGuard(self.settings).require_enabled()
                state["phase"] = "embedding_inflight"
                row = self.records.write("document", identifier, state, previous=row)
                vectors = self.embeddings.embed_texts([chunk["text"] for chunk in chunks])
                self.blobs.put(
                    identifier, "vectors.json", json.dumps(vectors).encode(), "application/json"
                )
                state["phase"] = "index"
                row = self.records.write("document", identifier, state, previous=row)
            vectors = json.loads(self.blobs.get(identifier, "vectors.json"))
            if len(vectors) != len(chunks):
                raise ManagedStoreError("Document vector integrity check failed")
            for chunk, vector in zip(chunks, vectors, strict=True):
                chunk["embedding"] = vector
            self.search_store.upsert(chunks)
            state.update(status="ready", phase="complete", chunks=len(chunks))
            self.records.write(
                "document", identifier, state, previous=row, fields={"status": "ready"}
            )
            self.records.write("indexed_hash", identifier, {"chunks": len(chunks)})
            return state
        except AzureUsageBlocked:
            # Safe pre-dispatch waiting is resumable; no inference has occurred.
            if state["phase"] in {"extract", "embed"}:
                self.records.write(
                    "document",
                    identifier,
                    {**state, "status": "waiting"},
                    previous=row,
                    fields={"status": "waiting"},
                )
            raise
        except Exception:
            # Preserve the in-flight phase when completion/accounting is ambiguous.
            self.records.write(
                "document",
                identifier,
                {**row["payload"], "status": "error"},
                previous=row,
                fields={"status": "error"},
            )
            raise
