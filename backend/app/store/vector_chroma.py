"""Persistent vector storage. Failures never reset or delete a collection."""

from typing import Any, Protocol

ChunkRecord = dict[str, Any]


class StorageError(RuntimeError):
    pass


class VectorStore(Protocol):
    def upsert(self, chunks: list[ChunkRecord]) -> None: ...
    def search(self, query_embedding: list[float], top_k: int = 5) -> list[ChunkRecord]: ...


class VectorChromaStore:
    def __init__(
        self,
        path: str,
        collection_name="chunks",
        embedding_provider=None,
        embedding_model=None,
        embedding_dimensions=384,
    ):
        import chromadb
        from chromadb.config import Settings

        self.path, self.collection_name = path, collection_name
        self.embedding_dimensions = embedding_dimensions
        self.embedding_provider, self.embedding_model = embedding_provider, embedding_model
        try:
            self._client = chromadb.PersistentClient(
                path=path, settings=Settings(anonymized_telemetry=False)
            )
            metadata = (
                {"embedding_provider": embedding_provider, "embedding_model": embedding_model}
                if embedding_provider
                else None
            )
            from chromadb.errors import InvalidCollectionException
            from chromadb.db.base import UniqueConstraintError

            try:
                self._collection = self._client.get_collection(
                    name=collection_name, embedding_function=None
                )
            except (InvalidCollectionException, ValueError) as missing:
                if (
                    isinstance(missing, ValueError)
                    and str(missing) != f"Collection {collection_name} does not exist."
                ):
                    raise
                try:
                    self._collection = self._client.create_collection(
                        name=collection_name, metadata=metadata, embedding_function=None
                    )
                except UniqueConstraintError:
                    self._collection = self._client.get_collection(
                        name=collection_name, embedding_function=None
                    )
            stored = self._collection.metadata or {}
            if embedding_provider and any(stored.get(k) != v for k, v in metadata.items()):
                raise StorageError(
                    "Collection embedding provenance does not match the configured model"
                )
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError("Chroma initialization failed") from exc

    def contains_hash(self, content_hash):
        try:
            return bool(self._collection.get(where={"content_hash": content_hash}, limit=1)["ids"])
        except Exception as exc:
            raise StorageError("Chroma duplicate lookup failed") from exc

    def upsert(self, chunks):
        if not chunks:
            return
        for c in chunks:
            meta = c.get("metadata", {})
            if self.embedding_provider and (
                meta.get("embedding_provider") != self.embedding_provider
                or meta.get("embedding_model") != self.embedding_model
            ):
                raise StorageError("Chunk embedding provenance does not match this collection")
            if self.embedding_provider and len(c["embedding"]) != self.embedding_dimensions:
                raise StorageError(f"Expected {self.embedding_dimensions}-dimensional embeddings")
        try:
            self._collection.upsert(
                ids=[c["id"] for c in chunks],
                documents=[c["text"] for c in chunks],
                metadatas=[c["metadata"] for c in chunks],
                embeddings=[c["embedding"] for c in chunks],
            )
        except Exception as exc:
            raise StorageError("Chroma upsert failed; stored data was preserved") from exc

    def search(self, query_embedding, top_k=5):
        try:
            count = self.count()
            if not count:
                return []
            result = self._collection.query(
                query_embeddings=[query_embedding], n_results=min(count, top_k)
            )
            return [
                {
                    "id": value,
                    "text": result["documents"][0][i],
                    "metadata": result["metadatas"][0][i],
                    "score": result["distances"][0][i],
                }
                for i, value in enumerate(result["ids"][0])
            ]
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError("Chroma search failed; stored data was preserved") from exc

    def search_identifiers(self, identifiers, top_k=5):
        """Recover exact opaque codes/device identifiers that embeddings may blur."""
        import re

        variants = list(
            dict.fromkeys(
                value
                for identifier in identifiers
                for value in (identifier, identifier.upper(), identifier.lower())
            )
        )
        filters = [{"$contains": value} for value in variants]
        where = filters[0] if len(filters) == 1 else {"$or": filters}
        try:
            result = self._collection.get(where_document=where, limit=min(top_k * 5, 150))
            rows = []
            for i, identifier in enumerate(result["ids"]):
                text = result["documents"][i]
                if not any(
                    re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", text, re.I)
                    for value in identifiers
                ):
                    continue
                rows.append(
                    {
                        "id": identifier,
                        "text": text,
                        "metadata": result["metadatas"][i],
                        "score": 0,
                        "match_type": "exact_identifier",
                    }
                )
            return rows[:top_k]
        except Exception as exc:
            raise StorageError(
                "Chroma identifier lookup failed; stored data was preserved"
            ) from exc

    def count(self):
        try:
            return self._collection.count()
        except Exception as exc:
            raise StorageError("Chroma count failed") from exc

    def search_keywords(self, question, top_k=6):
        """Read existing document text without generating a query embedding."""
        from backend.app.repositories.documents import query_terms
        import re

        terms = query_terms(question)
        if not terms:
            return []
        variants = list(
            dict.fromkeys(
                re.findall(r"[\w-]+", question)[:16]
                + [v for term in terms for v in (term, term.upper(), term.title())]
            )
        )
        filters = [{"$contains": value} for value in variants]
        where = filters[0] if len(filters) == 1 else {"$or": filters}
        try:
            found = self._collection.get(where_document=where, limit=200)
            rows = []
            for index, identifier in enumerate(found["ids"]):
                text = found["documents"][index]
                score = sum(term in text.casefold() for term in terms)
                if score:
                    rows.append(
                        {
                            "id": identifier,
                            "text": text,
                            "metadata": found["metadatas"][index],
                            "score": score,
                            "match_type": "keyword",
                            "storage": "chroma",
                        }
                    )
            return sorted(rows, key=lambda row: (-row["score"], row["id"]))[:top_k]
        except Exception as exc:
            raise StorageError("Chroma keyword lookup failed; stored data preserved") from exc

    def ping(self):
        try:
            self.count()
            return True
        except StorageError:
            return False


class InMemoryVectorStore:
    def __init__(self):
        self._records = {}

    def upsert(self, chunks):
        self._records.update({c["id"]: c for c in chunks})

    def contains_hash(self, content_hash):
        return any(
            c.get("metadata", {}).get("content_hash") == content_hash
            for c in self._records.values()
        )

    def search(self, query_embedding, top_k=5):
        return list(self._records.values())[:top_k]

    def count(self):
        return len(self._records)

    def ping(self):
        return True
