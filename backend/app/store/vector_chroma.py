from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Protocol

import chromadb
from chromadb.config import Settings as ChromaSettings

ChunkRecord = Dict[str, object]

logger = logging.getLogger("service-desk")


class VectorStore(Protocol):
    """Protocol describing the vector store interface used by the application."""

    def upsert(self, chunks: List[ChunkRecord]) -> None:
        """Persist embeddings and chunk metadata."""

    def search(self, query_embedding: List[float], top_k: int = 5) -> List[ChunkRecord]:
        """Return the top matching chunks for a query embedding."""

    def ping(self) -> bool:
        """Return True when the store is reachable."""


@dataclass
class VectorChromaStore:
    """Chroma-backed vector store used for similarity search."""

    path: str
    collection_name: str = "chunks"

    def __post_init__(self) -> None:
        """Initialise the persistent Chroma collection on first use."""
        os.makedirs(self.path, exist_ok=True)
        client_settings = ChromaSettings(is_persistent=True, anonymized_telemetry=False)
        self._client = chromadb.PersistentClient(path=self.path, settings=client_settings)
        self._collection = self._client.get_or_create_collection(name=self.collection_name)

    def upsert(self, chunks: List[ChunkRecord]) -> None:
        """Persist new or updated chunk embeddings."""

        if not chunks:
            return
        ids = [chunk["id"] for chunk in chunks]
        documents = [chunk.get("text", "") for chunk in chunks]
        metadatas = [chunk.get("metadata", {}) for chunk in chunks]
        embeddings = [chunk.get("embedding") for chunk in chunks]
        try:
            self._collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=embeddings,
            )
        except TypeError as exc:
            self._handle_metadata_corruption(exc)
            self._collection.upsert(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=embeddings,
            )

    def search(self, query_embedding: List[float], top_k: int = 5) -> List[ChunkRecord]:
        """Query the store for the top-k most similar chunks."""

        try:
            results = self._collection.query(query_embeddings=[query_embedding], n_results=top_k)
        except TypeError as exc:
            self._handle_metadata_corruption(exc)
            return []
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        formatted: List[ChunkRecord] = []
        for idx, chunk_id in enumerate(ids):
            formatted.append(
                {
                    "id": chunk_id,
                    "text": documents[idx] if idx < len(documents) else "",
                    "metadata": metadatas[idx] if idx < len(metadatas) else {},
                    "score": distances[idx] if idx < len(distances) else None,
                }
            )
        return formatted

    def ping(self) -> bool:
        """Return True when the underlying Chroma collection responds."""

        try:
            if hasattr(self._collection, "count"):
                self._collection.count()
            return True
        except Exception:  # pragma: no cover - runtime state dependent
            return False

    def _handle_metadata_corruption(self, exc: Exception) -> None:
        """Reset the Chroma collection when legacy metadata causes type errors."""

        logger.warning(
            "Detected incompatible Chroma metadata for collection %s at %s; resetting store (%s)",
            self.collection_name,
            self.path,
            exc,
        )
        self._reset_collection()

    def _reset_collection(self) -> None:
        """Drop and recreate the collection to recover from metadata incompatibilities."""

        try:
            self._client.delete_collection(name=self.collection_name)
        except Exception:
            try:
                self._client.reset()
            except Exception as reset_exc:  # pragma: no cover - defensive
                logger.error(
                    "Failed to reset Chroma persistent store at %s after metadata corruption: %s",
                    self.path,
                    reset_exc,
                )
                raise
        self._collection = self._client.get_or_create_collection(name=self.collection_name)


class InMemoryVectorStore(VectorStore):
    """Simple dictionary-backed vector store for tests and development."""

    def __init__(self) -> None:
        """Create an empty, deterministic in-memory record store."""
        self._records: Dict[str, ChunkRecord] = {}

    def upsert(self, chunks: List[ChunkRecord]) -> None:
        """Persist chunk embeddings into the in-memory dictionary."""

        for chunk in chunks:
            self._records[chunk["id"]] = chunk

    def search(self, query_embedding: List[float], top_k: int = 5) -> List[ChunkRecord]:  # pragma: no cover - simple fallback
        """Return the first N chunks; ignores the query embedding."""

        return list(self._records.values())[:top_k]

    def ping(self) -> bool:
        """In-memory store is always reachable."""

        return True
