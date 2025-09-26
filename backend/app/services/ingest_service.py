from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import Iterable, List, Mapping, Optional

from pdfminer.high_level import extract_text

from backend.app.adapters.embeddings import StubEmbeddingProvider
from backend.app.models.dto import IngestPasteResponse, IngestPdfResponse
from backend.app.services.chunking import approx_tokens, split_text
from backend.app.services.entities import extract_entities

logger = logging.getLogger("service-desk")


@dataclass
class IngestService:
    """Orchestrates chunking, embedding, and persistence for new documents."""

    settings: object
    vector_store: object
    graph_repo: object
    embedding_provider: object

    def ingest_text(self, title: Optional[str], text: str) -> IngestPasteResponse:
        """Ingest plain text by chunking, embedding, and writing to stores."""

        started = time.perf_counter()
        chunks = split_text(
            text,
            getattr(self.settings, "chunk_tokens", None),
            getattr(self.settings, "chunk_overlap", None),
        )
        if not chunks:
            return IngestPasteResponse(
                chunks=0,
                entities=0,
                vector_count=0,
                ms=int((time.perf_counter() - started) * 1000),
            )

        doc_id = uuid.uuid4().hex
        chunk_texts: List[str] = []
        records: List[Mapping[str, object]] = []
        for chunk in chunks:
            chunk_id = f"{doc_id}-{chunk['ord']}"
            chunk["chunk_id"] = chunk_id
            chunk["id"] = chunk_id
            chunk_text = str(chunk["text"])
            chunk_texts.append(chunk_text)
            chunk["metadata"] = {"doc_id": doc_id, "ord": chunk["ord"]}

        try:
            embeddings = self.embedding_provider.embed_texts(chunk_texts)
        except Exception as exc:  # pragma: no cover - runtime specific
            logger.warning("Embedding provider failed (%s); falling back to stub embeddings", exc)
            fallback = StubEmbeddingProvider()
            embeddings = fallback.embed_texts(chunk_texts)
            self.embedding_provider = fallback
        for chunk, embedding in zip(chunks, embeddings):
            records.append(
                {
                    "id": chunk["chunk_id"],
                    "text": chunk["text"],
                    "metadata": chunk.get("metadata", {}),
                    "embedding": embedding,
                }
            )

        self.vector_store.upsert(records)

        self.graph_repo.upsert_document(doc_id, title=title)
        for chunk in chunks:
            self.graph_repo.upsert_chunk(
                doc_id,
                chunk["chunk_id"],
                ord=chunk["ord"],
                text=str(chunk["text"]),
                token_count=approx_tokens(str(chunk["text"])),
            )
            self.graph_repo.link_doc_chunk(doc_id, chunk["chunk_id"])

        entities = extract_entities(chunks)
        for entity in entities:
            entity_id = self.graph_repo.upsert_entity(entity)
            for chunk in chunks:
                if entity in str(chunk["text"]).lower():
                    self.graph_repo.link_chunk_entity(chunk["chunk_id"], entity_id)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return IngestPasteResponse(
            chunks=len(chunks),
            entities=len(entities),
            vector_count=len(records),
            ms=elapsed_ms,
        )

    def ingest_pdf(self, title: Optional[str], data: bytes) -> IngestPdfResponse:
        """Ingest a PDF document by extracting text then delegating to ingest_text."""

        started = time.perf_counter()
        text = extract_text(BytesIO(data))
        page_count = text.count("\f") + 1 if text else 0
        result = self.ingest_text(title=title, text=text)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return IngestPdfResponse(
            pages=page_count,
            chunks=result.chunks,
            entities=result.entities,
            vector_count=result.vector_count,
            ms=elapsed_ms,
        )
