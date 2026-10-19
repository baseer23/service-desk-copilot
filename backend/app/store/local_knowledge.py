"""Tenant-scoped persistent Chroma knowledge and immutable private local sources."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from backend.app.store.records import ManagedStoreError
from backend.app.services.knowledge_policy import is_test_knowledge


class LocalDocuments:
    def __init__(self, settings, records):
        self.root = settings.local_documents_dir.resolve()
        self.records = records
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def path(self, document_id, artifact):
        valid_artifact = artifact in {"original", "extraction.json", "vectors.json"} or bool(
            re.fullmatch(r"vectors-[a-f0-9]{16}\.json", artifact)
            or re.fullmatch(r"figure-[a-f0-9]{16}\.bin", artifact)
            or re.fullmatch(r"extraction-batch-\d{1,6}\.json", artifact)
            or re.fullmatch(r"extraction-[a-f0-9]{16}\.json", artifact)
        )
        if not re.fullmatch(r"[a-f0-9]{64}", document_id) or not valid_artifact:
            raise ValueError("Unsupported document artifact")
        return (
            hashlib.sha256(self.records.tenant().encode()).hexdigest()
            + "/"
            + document_id
            + "/"
            + artifact
        )

    def location(self, document_id, artifact):
        path = self.root / self.path(document_id, artifact)
        if not path.resolve().is_relative_to(self.root):
            raise ManagedStoreError("Invalid document location")
        return path

    def put(self, document_id, artifact, data, media_type):
        path = self.location(document_id, artifact)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
            try:
                os.link(temporary, path)  # Atomic publication, never overwrite an original.
            except FileExistsError:
                if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(data).digest():
                    raise ManagedStoreError("Document artifact integrity mismatch") from None
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return self.path(document_id, artifact)

    def put_file(self, document_id, artifact, source, media_type):
        path = self.location(document_id, artifact)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent)
        try:
            with open(source, "rb") as reader, os.fdopen(descriptor, "wb") as target:
                while chunk := reader.read(1024 * 1024):
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                def digest(value):
                    result = hashlib.sha256()
                    with Path(value).open("rb") as stream:
                        while chunk := stream.read(1024 * 1024):
                            result.update(chunk)
                    return result.digest()

                if digest(path) != digest(temporary):
                    raise ManagedStoreError("Document artifact integrity mismatch") from None
        finally:
            Path(temporary).unlink(missing_ok=True)
        return self.path(document_id, artifact)

    def get(self, document_id, artifact):
        path = self.location(document_id, artifact)
        try:
            return path.read_bytes()
        except OSError:
            raise ManagedStoreError("Document could not be read securely") from None


class LocalKnowledgeStore:
    def __init__(self, vector, records):
        self.vector, self.records = vector, records

    def physical_id(self, identifier):
        return hashlib.sha256((self.records.tenant() + ":" + identifier).encode()).hexdigest()

    def upsert(self, chunks):
        ready = []
        for chunk in chunks:
            if chunk.get("embedding") is None:
                # Transactional staging belongs to the resumable ingestion job.
                self.records.write("knowledge_pending", chunk["id"], chunk)
                continue
            metadata = chunk["metadata"]
            ready.append(
                {
                    **chunk,
                    "id": self.physical_id(chunk["id"]),
                    "metadata": {
                        **{
                            k: v
                            for k, v in metadata.items()
                            if isinstance(v, str | int | float | bool)
                        },
                        "tenant_id": self.records.tenant(),
                        "chunk_id": chunk["id"],
                        "metadata_json": json.dumps(metadata),
                    },
                }
            )
        self.vector.upsert(ready)
        if ready:
            with self.records.transaction() as db:
                for chunk in chunks:
                    if chunk.get("embedding") is not None:
                        db.execute(
                            "DELETE FROM local_records WHERE tenant=? "
                            "AND kind='knowledge_pending' AND record_key=?",
                            (self.records.tenant(), chunk["id"]),
                        )

    def row(self, identifier, text, metadata, score=0, **extra):
        meta = json.loads(metadata["metadata_json"])
        if not self.records.settings.test_mode and is_test_knowledge(meta):
            return None
        if meta.get("family"):
            family = self.records.read("document_family", meta["family"])
            if family and family["payload"].get("published") not in {None, meta.get("doc_id")}:
                return None
        return {
            "id": metadata.get("chunk_id", identifier),
            "text": text,
            "metadata": meta,
            "score": score,
            "storage": "chroma",
            **extra,
        }

    def search(self, query_embedding, top_k=6):
        if not self.count():
            return []
        found = self.vector._collection.query(
            query_embeddings=[query_embedding],
            where={"tenant_id": self.records.tenant()},
            n_results=min(self.count(), top_k * 3),
        )
        rows = [
            self.row(
                identifier,
                found["documents"][0][i],
                found["metadatas"][0][i],
                found["distances"][0][i],
            )
            for i, identifier in enumerate(found["ids"][0])
        ]
        return [row for row in rows if row][:top_k]

    def search_keywords(self, question, top_k=6):
        from backend.app.repositories.documents import (
            explicit_publication_source, is_publication, lexical_score, query_terms,
        )

        terms = query_terms(question)
        if not terms:
            return []
        rows = {}
        # Every historical embedding space remains intact and lexical-only.
        collections = [self.vector._collection] + [
            c for c in self.vector._client.list_collections()
            if c.name != self.vector.collection_name
            and (c.name.startswith("archive_") or c.name.startswith("chunks_azure_"))
        ]
        for collection in collections:
            offset = 0
            while True:
                found = collection.get(
                    where={"tenant_id": self.records.tenant()}, limit=200, offset=offset
                )
                for i, identifier in enumerate(found["ids"]):
                    text = found["documents"][i]
                    meta = json.loads(found["metadatas"][i]["metadata_json"])
                    score = lexical_score(question, text, meta)
                    row = self.row(
                        identifier, text, found["metadatas"][i], score, match_type="keyword"
                    )
                    if score and row:
                        rows.setdefault(row["id"], row)
                if len(found["ids"]) < 200:
                    break
                offset += 200
        for entry in self.records.query("knowledge_pending", limit=10000):
            chunk = entry["payload"]
            score = lexical_score(question, chunk["text"], chunk.get("metadata"))
            if score and chunk["id"] not in rows:
                row = self.row(
                    chunk["id"],
                    chunk["text"],
                    {"metadata_json": json.dumps(chunk["metadata"])},
                    score,
                    match_type="keyword",
                )
                if row:
                    rows[chunk["id"]] = row
        ranked = sorted(rows.values(), key=lambda row: (-row["score"], row["id"]))
        if explicit_publication_source(question):
            publication_rows = [row for row in ranked if is_publication(row.get("metadata"))]
            if publication_rows:
                ranked = publication_rows
        return ranked[:top_k]

    def search_identifiers(self, identifiers, top_k=6):
        rows = self.search_keywords(" ".join(identifiers), max(top_k * 5, 30))
        return [
            row
            for row in rows
            if any(
                re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", row["text"], re.I)
                for value in identifiers
            )
        ][:top_k]

    def hybrid(self, question, query_embedding, top_k=6):
        rows = {r["id"]: r for r in self.search_keywords(question, top_k)}
        for row in self.search(query_embedding, top_k):
            rows.setdefault(row["id"], row)
        return list(rows.values())[:top_k]

    def resolve_object_ids(self, object_ids):
        """Hydrate tenant-owned graph references from local Chroma content."""
        wanted = set(object_ids)
        if not wanted:
            return []
        found = self.vector._collection.get(
            where={"tenant_id": self.records.tenant()}, include=["documents", "metadatas"]
        )
        rows = []
        for index, identifier in enumerate(found["ids"]):
            metadata = found["metadatas"][index]
            meta = json.loads(metadata["metadata_json"])
            if meta.get("object_id") not in wanted or meta.get("ingestion_mode") != "structured_graph":
                continue
            row = self.row(identifier, found["documents"][index], metadata)
            if row:
                rows.append(row)
        return rows

    def contains_hash(self, digest):
        return bool(self.records.read("indexed_hash", digest)) or bool(
            self.vector._collection.get(
                where={"$and": [{"tenant_id": self.records.tenant()}, {"content_hash": digest}]},
                limit=1,
            )["ids"]
        )

    def count(self):
        return len(
            self.vector._collection.get(where={"tenant_id": self.records.tenant()}, include=[])[
                "ids"
            ]
        )

    def ping(self):
        return self.vector.ping()
