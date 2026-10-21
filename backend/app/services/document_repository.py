"""Resumable local document jobs, immutable artifacts and verified Chroma indexing."""

import hashlib
import json
from pathlib import Path

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
        if not data or len(title or "") > 300:
            raise ValueError("Choose a nonempty document and a title up to 300 characters")
        if media_type in {"text/plain", "text/markdown"} and not data.decode("utf-8-sig").strip():
            raise ValueError("The document contains no readable text")
        identifier = hashlib.sha256(data).hexdigest()
        previous = self.records.read("document", identifier)
        if previous:
            payload = self._upgrade_duplicate(
                identifier, previous["payload"], ingestion_mode, document_processing
            )
            return {
                "id": identifier, "duplicate": True,
                "chunks": payload.get("chunks", 0), "status": payload["status"],
                "storage": "local", "indexed": payload["status"] == "ready",
                "ingestion_mode": payload.get("ingestion_mode", "standard"),
                "document_processing": payload.get("document_processing", "local"),
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

    def upload_path(
        self, source, title, media_type, ingestion_mode="standard", document_processing="local"
    ):
        path = Path(source)
        digest, size = hashlib.sha256(), 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        if not size:
            raise ValueError("Choose a nonempty document")
        identifier = digest.hexdigest()
        previous = self.records.read("document", identifier)
        if previous:
            payload = self._upgrade_duplicate(
                identifier, previous["payload"], ingestion_mode, document_processing
            )
            return {**payload, "duplicate": True, "storage": "local", "indexed": payload["status"] == "ready"}
        if ingestion_mode not in {"standard", "structured_graph"}:
            raise ValueError("ingestion_mode must be standard or structured_graph")
        if document_processing not in {"local", "azure_di"}:
            raise ValueError("document_processing must be local or azure_di")
        if len(title or "") > 300:
            raise ValueError("Choose a title up to 300 characters")
        self.blobs.put_file(identifier, "original", path, media_type)
        payload = {
            "id": identifier, "title": title or "Untitled",
            "source_type": "pdf" if media_type == "application/pdf" else "document",
            "media_type": media_type, "status": "queued", "phase": "extract",
            "chunks": 0, "pages": 0, "ingestion_mode": ingestion_mode,
            "document_processing": document_processing,
        }
        family = hashlib.sha256((title or identifier).strip().casefold().encode()).hexdigest()
        with self.records.locked("document_family", family) as history:
            versions = history.setdefault("versions", [])
            payload.update(family=family, version=len(versions) + 1, content_hash=identifier)
            self.records.write("document", identifier, payload, create_only=True, fields={"status": "queued"})
            versions.append(identifier)
        return {**payload, "duplicate": False, "storage": "local", "indexed": False}

    def _upgrade_duplicate(self, identifier, previous, ingestion_mode, document_processing):
        """Upgrade local duplicates once, preserving ambiguous paid-operation guards."""
        if ingestion_mode != "structured_graph" or document_processing != "azure_di":
            return previous
        if previous.get("document_processing") != "azure_di":
            updated = self.reprocess(identifier, document_processing="azure_di")
            row = self.records.read("document", identifier)
            updated = {**updated, "ingestion_mode": "structured_graph", "graph_link_status": "pending"}
            self.records.write("document", identifier, updated, previous=row, fields={"status": updated["status"]})
            return updated
        if previous.get("ingestion_mode") != "structured_graph":
            return self.prepare_graph_enrichment(identifier)
        if previous.get("status") == "graph_pending" and previous.get("phase") == "link":
            row = self.records.read("document", identifier)
            updated = {**previous, "status": "queued"}
            self.records.write("document", identifier, updated, previous=row, fields={"status": "queued"})
            return updated
        return previous

    def save(self, text: str, title: str | None, source_type: str, pages: int = 1):
        return self.upload(text.encode(), title, "text/plain")

    def list_documents(self):
        return [row["payload"] for row in self.records.query("document", limit=100)]

    def get(self, identifier: str):
        row = self.records.read("document", identifier)
        if not row:
            raise ValueError("Document not found")
        return row["payload"]

    def reprocess(
        self, identifier: str, document_processing: str = "azure_di",
        operator_reviewed_inflight: bool = False,
    ):
        row = self.records.read("document", identifier)
        if not row:
            raise ValueError("Document not found")
        state = dict(row["payload"])
        if state.get("phase") in {"extract_inflight", "embedding_inflight"} and not (
            operator_reviewed_inflight and state.get("status") == "error"
        ):
            raise ManagedStoreError("This document has an ambiguous in-flight operation")
        history = list(state.get("extraction_history", []))
        if state.get("extraction_artifact"):
            history.append(state["extraction_artifact"])
        elif self.blobs.location(identifier, "extraction.json").exists():
            history.append("extraction.json")
        state.update(
            status="queued", phase="extract", document_processing=document_processing,
            extraction_history=history, reprocess_generation=state.get("reprocess_generation", 0) + 1,
        )
        state.pop("extraction_artifact", None)
        self.records.write("document", identifier, state, previous=row, fields={"status": "queued"})
        return state

    def prepare_graph_enrichment(self, identifier: str):
        """Rebuild local structured objects from saved extraction, never Azure DI."""
        row = self.records.read("document", identifier)
        if not row:
            raise ValueError("Document not found")
        state = dict(row["payload"])
        artifact = state.get("extraction_artifact", "extraction.json")
        try:
            extraction = json.loads(self.blobs.get(identifier, artifact))
        except Exception:
            raise ManagedStoreError(
                "Saved extraction is unavailable; graph enrichment cannot proceed safely"
            ) from None
        if (
            state.get("phase") in {"extract_inflight", "embedding_inflight"}
            and not state.get("chunks")
        ):
            raise ManagedStoreError("This document has an ambiguous in-flight operation")
        phase = "structure"
        structured_texts = [
            item["text"]
            for item in split_structured(
                identifier, extraction, self.settings.chunk_tokens, state.get("version", 1)
            )
        ]
        reusable = getattr(
            self.search_store, "reusable_vectors", lambda _identifier, _texts: None
        )(identifier, structured_texts)
        if reusable:
            # Embeddings are a pure function of text. Reuse the verified saved
            # vectors when graph enrichment only adds stable object metadata.
            encoded = json.dumps(reusable).encode()
            state["vector_artifact"] = (
                "vectors-" + hashlib.sha256(encoded).hexdigest()[:16] + ".json"
            )
            self.blobs.put(
                identifier, state["vector_artifact"], encoded, "application/json"
            )
            phase = "index"
        state.update(
            ingestion_mode="structured_graph",
            status="structuring",
            phase=phase,
            graph_link_status="pending",
        )
        self.records.write(
            "document", identifier, state, previous=row, fields={"status": "structuring"}
        )
        return state

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
                location = getattr(self.blobs, "location", None)
                original_path = location(identifier, "original") if location else None
                if state["media_type"] in {"text/plain", "text/markdown"}:
                    original = (
                        original_path.read_text(encoding="utf-8-sig")
                        if original_path
                        else self.blobs.get(identifier, "original").decode("utf-8-sig")
                    )
                    result = {
                        "pages": [
                            {
                                "page": 1,
                                "text": original,
                                "markdown": original,
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
                    def load_batch(index):
                        path = self.blobs.location(identifier, f"extraction-batch-{index}.json")
                        return json.loads(path.read_bytes()) if path.exists() else None

                    def save_batch(index, batch):
                        nonlocal row
                        self.blobs.put(
                            identifier, f"extraction-batch-{index}.json",
                            json.dumps(batch).encode(), "application/json",
                        )
                        state.update(
                            completed_batches=index + 1,
                            pages=sum(
                                len(load_batch(item).get("pages", []))
                                for item in range(index + 1)
                            ),
                        )
                        row = self.records.write(
                            "document", identifier, state, previous=row,
                            fields={"status": "extracting"},
                        )
                    if original_path and state.get("document_processing") == "azure_di":
                        result = self.extractor.extract_path(
                            original_path, state["media_type"], True, force_azure=True,
                            load_batch=load_batch, save_batch=save_batch,
                        )
                    elif original_path and state.get("ingestion_mode") == "structured_graph":
                        result = self.extractor.extract_path(original_path, state["media_type"], True)
                    elif original_path:
                        result = self.extractor.extract_path(original_path, state["media_type"])
                    else:
                        original = self.blobs.get(identifier, "original")
                        result = self.extractor.extract(
                            original,
                            state["media_type"],
                            state.get("ingestion_mode") == "structured_graph",
                        )
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
                encoded_result = json.dumps(result).encode()
                extraction_artifact = (
                    "extraction-" + hashlib.sha256(encoded_result).hexdigest()[:16] + ".json"
                )
                self.blobs.put(identifier, extraction_artifact, encoded_result, "application/json")
                # Preserve the first-generation compatibility pointer as an
                # immutable artifact. Reprocessing writes only content-addressed
                # generations and never overwrites this original extraction.
                try:
                    self.blobs.get(identifier, "extraction.json")
                except (ManagedStoreError, KeyError):
                    self.blobs.put(
                        identifier, "extraction.json", encoded_result, "application/json"
                    )
                state["extraction_artifact"] = extraction_artifact
                state.update(phase="structure", status="structuring", pages=len(result["pages"]))
                row = self.records.write(
                    "document", identifier, state, previous=row, fields={"status": "structuring"}
                )
            result = json.loads(
                self.blobs.get(identifier, state.get("extraction_artifact", "extraction.json"))
            )
            extraction_metadata = result.get("metadata", {})
            chunks = []
            ordinal = 0
            if state.get("ingestion_mode") == "structured_graph":
                structured = split_structured(
                    identifier, result, self.settings.chunk_tokens, state.get("version", 1)
                )
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
                "document", identifier, state, previous=row, fields={"status": state["status"]}
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
