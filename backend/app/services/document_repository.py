"""Resumable local document jobs, immutable artifacts and verified Chroma indexing."""

import hashlib
import json

from backend.app.adapters.embeddings import get_embedding_provider
from backend.app.core.config import Settings
from backend.app.services.chunking import split_text, split_structured
from backend.app.store.records import ManagedStoreError


class DocumentRepository:
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
        if records is None or blobs is None or search is None or extractor is None:
            raise ValueError("Local document storage and processing adapters are required")
        self.records = records
        self.blobs = blobs
        self.search_store = search
        self.extractor = extractor
        self.embeddings = embeddings or get_embedding_provider(settings)

    def upload(
        self,
        data: bytes,
        title: str | None,
        media_type: str,
        ingestion_mode: str = "standard",
        document_processing: str = "local",
    ):
        if ingestion_mode not in {"standard", "structured_graph"}:
            raise ValueError("ingestion_mode must be standard or structured_graph")
        if document_processing not in {"local", "azure_di"}:
            raise ValueError("document_processing must be local or azure_di")
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
                "storage": "local",
                "indexed": previous["payload"]["status"] == "ready",
                "ingestion_mode": previous["payload"].get("ingestion_mode", "standard"),
                "document_processing": previous["payload"].get("document_processing", "local"),
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
            "ingestion_mode": ingestion_mode,
            "document_processing": document_processing,
        }
        family = hashlib.sha256((title or identifier).strip().casefold().encode()).hexdigest()
        with self.records.locked("document_family", family) as history:
            versions = history.setdefault("versions", [])
            payload.update(family=family, version=len(versions) + 1, content_hash=identifier)
            self.records.write(
                "document", identifier, payload, create_only=True, fields={"status": "queued"}
            )
            versions.append(identifier)
        return {**payload, "duplicate": False, "storage": "local", "indexed": False}

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
        if state["phase"] == "extract_inflight":
            raise ManagedStoreError("This document needs an operator review before retrying")
        if state["phase"] == "embedding_inflight":
            # Local computation has no ambiguous external charge and can safely
            # resume after a process crash. Original artifacts remain immutable.
            state["phase"] = "embed"
            row = self.records.write("document", identifier, state, previous=row)
        try:
            if state["phase"] == "extract":
                original = self.blobs.get(identifier, "original")
                if state["media_type"] in {"text/plain", "text/markdown"}:
                    result = {
                        "pages": [
                            {
                                "page": 1,
                                "text": original.decode("utf-8-sig"),
                                "markdown": original.decode("utf-8-sig"),
                                "spans": [],
                            }
                        ],
                        "paragraphs": [],
                        "sections": [],
                        "tables": [],
                        "figures": [],
                        "method": "native_text",
                        "metadata": {
                            "structured": state.get("ingestion_mode") == "structured_graph",
                            "image_understanding": "not_applicable",
                        },
                    }
                else:
                    state.update(phase="extract_inflight", status="extracting")
                    row = self.records.write("document", identifier, state, previous=row)
                    if state.get("document_processing") == "azure_di":
                        result = self.extractor.extract(
                            original, state["media_type"], True, True
                        )
                    elif state.get("ingestion_mode") == "structured_graph":
                        result = self.extractor.extract(original, state["media_type"], True)
                    else:
                        result = self.extractor.extract(original, state["media_type"])
                for figure in result.get("figures", []):
                    encoded = figure.pop("image_base64", None)
                    if encoded:
                        import base64

                        artifact = (
                            "figure-"
                            + hashlib.sha256(str(figure["id"]).encode()).hexdigest()[:16]
                            + ".bin"
                        )
                        self.blobs.put(
                            identifier,
                            artifact,
                            base64.b64decode(encoded),
                            "application/octet-stream",
                        )
                        figure["image_artifact"] = artifact
                self.blobs.put(
                    identifier, "extraction.json", json.dumps(result).encode(), "application/json"
                )
                state.update(phase="structure", status="structuring", pages=len(result["pages"]))
                row = self.records.write(
                    "document", identifier, state, previous=row, fields={"status": "structuring"}
                )
            result = json.loads(self.blobs.get(identifier, "extraction.json"))
            extraction_metadata = result.get("metadata", {})
            chunks = []
            ordinal = 0
            if state.get("ingestion_mode") == "structured_graph":
                structured = split_structured(identifier, result, self.settings.chunk_tokens)
                for ordinal, item in enumerate(structured):
                    chunks.append(
                        {
                            "id": item["id"],
                            "text": item["text"],
                            "embedding": None,
                            "metadata": {
                                "doc_id": identifier,
                                "title": state["title"],
                                "source_type": state["source_type"],
                                "page": item["page"],
                                "ord": ordinal,
                                "object_id": item["object_id"],
                                "object_type": item["object_type"],
                                "section_id": item.get("section_id") or "",
                                "table_id": item.get("table_id") or "",
                                "figure_id": item.get("figure_id") or "",
                                "image_artifact": item.get("image_artifact") or "",
                                "ingestion_mode": "structured_graph",
                                "extraction_method": result.get("method", "unknown"),
                                "document_classification": extraction_metadata.get(
                                    "classification", "text_document"
                                ),
                                "table_count": sum(
                                    1
                                    for table in result.get("tables", [])
                                    if table["page"] == item["page"]
                                ),
                                "source": "/documents/" + identifier + "/download",
                                "content_hash": identifier,
                                "version": state.get("version", 1),
                                "family": state.get("family"),
                                "embedding_provider": getattr(
                                    self.embeddings, "provider", self.settings.embed_provider
                                ),
                                "embedding_model": getattr(
                                    self.embeddings, "model_name", self.settings.embed_model_name
                                ),
                            },
                        }
                    )
            else:
                for page in result["pages"]:
                    text = page["text"]
                    matching_tables = [
                        table for table in result["tables"] if table["page"] == page["page"]
                    ]
                    if matching_tables:
                        rendered_tables = []
                        for table in matching_tables:
                            rows = {}
                            for cell in sorted(
                                table["cells"], key=lambda item: (item["row"], item["column"])
                            ):
                                rows.setdefault(cell["row"], []).append(cell["text"])
                            rendered_tables.append(
                                "\n".join(" | ".join(cells) for cells in rows.values())
                            )
                        text += "\n" + "\n\n".join(rendered_tables)
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
                                    "extraction_method": result.get("method", "unknown"),
                                    "document_classification": extraction_metadata.get(
                                        "classification", "text_document"
                                    ),
                                    "table_count": sum(
                                        1
                                        for table in result["tables"]
                                        if table["page"] == page["page"]
                                    ),
                                    "source": "/documents/" + identifier + "/download",
                                    "content_hash": identifier,
                                    "version": state.get("version", 1),
                                    "family": state.get("family"),
                                    "embedding_provider": getattr(
                                        self.embeddings, "provider", self.settings.embed_provider
                                    ),
                                    "embedding_model": getattr(
                                        self.embeddings,
                                        "model_name",
                                        self.settings.embed_model_name,
                                    ),
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
            if state["phase"] in {"structure", "embed"}:
                state.update(phase="embed", status="embedding")
                row = self.records.write("document", identifier, state, previous=row)
                state["phase"] = "embedding_inflight"
                row = self.records.write("document", identifier, state, previous=row)
                vectors = self.embeddings.embed_texts([chunk["text"] for chunk in chunks])
                state["vector_artifact"] = (
                    "vectors-"
                    + hashlib.sha256(
                        (
                            self.settings.embed_provider + ":" + self.settings.embed_model_name
                        ).encode()
                    ).hexdigest()[:16]
                    + ".json"
                )
                self.blobs.put(
                    identifier,
                    state["vector_artifact"],
                    json.dumps(vectors).encode(),
                    "application/json",
                )
                state["phase"] = "index"
                row = self.records.write("document", identifier, state, previous=row)
            vectors = json.loads(
                self.blobs.get(identifier, state.get("vector_artifact", "vectors.json"))
            )
            if len(vectors) != len(chunks):
                raise ManagedStoreError("Document vector integrity check failed")
            for chunk, vector in zip(chunks, vectors, strict=True):
                chunk["embedding"] = vector
            self.search_store.upsert(chunks)
            state.update(
                status="linking" if state.get("ingestion_mode") == "structured_graph" else "ready",
                phase="link" if state.get("ingestion_mode") == "structured_graph" else "complete",
                chunks=len(chunks),
                extraction_method=result.get("method", "unknown"),
                tables=len(result["tables"]),
            )
            self.records.write(
                "document", identifier, state, previous=row, fields={"status": "ready"}
            )
            self.records.write("indexed_hash", identifier, {"chunks": len(chunks)})
            return state
        except Exception:
            # Preserve the in-flight phase when completion/accounting is ambiguous.
            failed = {**row["payload"], "status": "error"}
            if failed.get("phase") == "embedding_inflight":
                failed["phase"] = "embed"
            self.records.write(
                "document",
                identifier,
                failed,
                previous=row,
                fields={"status": "error"},
            )
            raise
