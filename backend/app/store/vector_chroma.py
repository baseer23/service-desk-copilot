from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import os

import chromadb
from chromadb.config import Settings as ChromaSettings


@dataclass
class VectorChromaStore:
    path: str
    collection_name: str = "chunks"

    def __post_init__(self) -> None:
        os.makedirs(self.path, exist_ok=True)
        client_settings = ChromaSettings(is_persistent=True, anonymized_telemetry=False)
        self._client = chromadb.PersistentClient(path=self.path, settings=client_settings)
        self._collection = self._client.get_or_create_collection(name=self.collection_name)

    def upsert(self, chunks: List[Dict[str, object]]) -> None:
        if not chunks:
            return
        ids = [chunk["id"] for chunk in chunks]
        documents = [chunk.get("text", "") for chunk in chunks]
        metadatas = [chunk.get("metadata", {}) for chunk in chunks]
        embeddings = [chunk.get("embedding") for chunk in chunks]
        self._collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)

    def search(self, query_embedding: List[float], top_k: int = 5) -> List[Dict[str, object]]:
        results = self._collection.query(query_embeddings=[query_embedding], n_results=top_k)
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        formatted: List[Dict[str, object]] = []
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


class InMemoryVectorStore:
    def __init__(self):
        self._records: Dict[str, Dict[str, object]] = {}

    def upsert(self, chunks: List[Dict[str, object]]) -> None:
        for chunk in chunks:
            self._records[chunk["id"]] = chunk

    def search(self, query_embedding: List[float], top_k: int = 5) -> List[Dict[str, object]]:  # pragma: no cover - simple fallback
        return list(self._records.values())[:top_k]
