"""Private Blob originals and tenant-filtered hybrid/semantic Azure AI Search."""

import hashlib
import json
from typing import Any

from backend.app.core.config import Settings
from backend.app.store.azure_records import CosmosRecords, ManagedStoreError, azure_credential


class AzureBlobDocuments:
    def __init__(
        self, settings: Settings, container: Any = None, records: CosmosRecords | None = None
    ):
        self.settings = settings
        self.records = records or CosmosRecords(settings)
        self.credential = None
        self.client = None
        if container is None:
            from azure.storage.blob import BlobServiceClient

            self.credential = azure_credential(settings)
            self.client = BlobServiceClient(
                settings.azure_storage_account_url,
                credential=self.credential,
                retry_total=0,
                connection_timeout=5,
                read_timeout=15,
            )
            container = self.client.get_container_client(settings.azure_storage_container)
        self.container = container

    def reserve_operation(self, kind):
        import time

        month = time.strftime("%Y-%m", time.gmtime())
        with self.records.locked("blob_operations", month, tenant="system:storage") as usage:
            if usage.get(kind, 0) >= 10_000:
                raise ManagedStoreError("Document storage request allowance is full")
            usage[kind] = usage.get(kind, 0) + 1

    def path(self, document_id: str, artifact: str) -> str:
        if artifact not in {"original", "extraction.json", "vectors.json"}:
            raise ValueError("Unsupported document artifact")
        return (
            hashlib.sha256(self.records.tenant().encode()).hexdigest()
            + "/"
            + document_id
            + "/"
            + artifact
        )

    def put(self, document_id: str, artifact: str, data: bytes, media_type: str):
        from azure.core.exceptions import ResourceExistsError
        from azure.storage.blob import ContentSettings

        path = self.path(document_id, artifact)
        self.reserve_operation("writes")
        # Reserve physical bytes before writing; a crash retains the conservative
        # charge. Account-wide limits cover all tenant partitions and artifacts.
        with self.records.locked("blob_quota", "account", tenant="system:storage") as quota:
            hashes = quota.setdefault("objects", {})
            if path not in hashes:
                total = sum(hashes.values())
                if total + len(data) > 100_000_000 or len(hashes) >= 1000:
                    raise ManagedStoreError("Document storage allowance is full")
                hashes[path] = len(data)
        try:
            self.container.upload_blob(
                path,
                data,
                overwrite=False,
                content_settings=ContentSettings(content_type=media_type),
                metadata={"sha256": hashlib.sha256(data).hexdigest()},
            )
        except ResourceExistsError:
            # Immutable artifacts may be retried, but never replaced with different data.
            self.reserve_operation("reads")
            existing = self.container.get_blob_client(path).get_blob_properties()
            if existing.metadata.get("sha256") != hashlib.sha256(data).hexdigest():
                raise ManagedStoreError("Document artifact integrity mismatch") from None
        except Exception:  # noqa: BLE001 - Sanitize SDK/auth failures without disclosing response bodies.
            raise ManagedStoreError("Document could not be stored securely") from None
        return path

    def get(self, document_id: str, artifact: str) -> bytes:
        self.reserve_operation("reads")
        try:
            stream = self.container.download_blob(
                self.path(document_id, artifact), max_concurrency=1
            )
            if stream.size > 25_000_000:
                raise ManagedStoreError("Document artifact exceeds the safe download limit")
            return stream.readall()
        except Exception:  # noqa: BLE001 - Sanitize SDK/auth failures without disclosing response bodies.
            raise ManagedStoreError("Document could not be read securely") from None


class AzureSearchStore:
    def __init__(
        self, settings: Settings, client: Any = None, records: CosmosRecords | None = None
    ):
        self.settings = settings
        self.records = records or CosmosRecords(settings)
        self.credential = None
        if client is None:
            from azure.search.documents import SearchClient

            self.credential = azure_credential(settings)
            client = SearchClient(
                settings.azure_search_endpoint,
                settings.azure_search_index,
                credential=self.credential,
                retry_total=0,
                connection_timeout=5,
                read_timeout=10,
            )
        self.client = client

    def upsert(self, chunks):
        documents = []
        for chunk in chunks:
            metadata = chunk["metadata"]
            documents.append(
                {
                    "id": hashlib.sha256(
                        (self.records.tenant() + ":" + chunk["id"]).encode()
                    ).hexdigest(),
                    "tenant_id": self.records.tenant(),
                    "chunk_id": chunk["id"],
                    "doc_id": metadata["doc_id"],
                    "text": chunk["text"],
                    "title": metadata.get("title", "Untitled"),
                    "source_type": metadata.get("source_type", "document"),
                    "source": metadata.get("source", ""),
                    "page": metadata.get("page", 1),
                    "metadata_json": json.dumps(metadata),
                }
            )
            if chunk.get("embedding") is not None:
                if len(chunk["embedding"]) != self.settings.azure_embedding_dimensions:
                    raise ManagedStoreError("Knowledge vector dimensions do not match the index")
                documents[-1]["vector"] = chunk["embedding"]
        try:
            for start in range(0, len(documents), 50):
                results = self.client.upload_documents(documents[start : start + 50])
                if not all(result.succeeded for result in results):
                    raise ManagedStoreError("Some document sections could not be indexed")
            if chunks and all(chunk.get("embedding") is not None for chunk in chunks):
                for digest in {chunk["metadata"].get("content_hash") for chunk in chunks} - {None}:
                    self.records.write("indexed_hash", digest, {"chunks": len(chunks)})
        except Exception:  # noqa: BLE001 - Sanitize SDK/auth failures without disclosing response bodies.
            raise ManagedStoreError(
                "Knowledge indexing failed; saved documents were preserved"
            ) from None

    def _search(self, question: str, embedding=None, top_k=6):
        from azure.search.documents.models import VectorizedQuery

        tenant = self.records.tenant().replace("'", "''")
        options: dict[str, Any] = {
            "search_text": question,
            "top": min(50, top_k * 3),
            "filter": f"tenant_id eq '{tenant}'",
            "select": ["chunk_id", "doc_id", "text", "title", "page", "metadata_json"],
        }
        if embedding is not None:
            options["vector_queries"] = [
                VectorizedQuery(
                    vector=embedding, k_nearest_neighbors=max(top_k, 10), fields="vector"
                )
            ]
        if self.settings.azure_search_semantic_enabled and question.strip():
            options.update(query_type="semantic", semantic_configuration_name="grounded")
        try:
            results = self.client.search(**options)
            rows = []
            for row in results:
                meta = json.loads(row["metadata_json"])
                if meta.get("family"):
                    family = self.records.read("document_family", meta["family"])
                    if family and family["payload"].get("published") not in {
                        None,
                        meta.get("doc_id"),
                    }:
                        continue
                rows.append(
                    {
                        "id": row["chunk_id"],
                        "text": row["text"],
                        "metadata": meta,
                        "score": row.get("@search.reranker_score", row.get("@search.score", 0)),
                        "storage": "azure",
                        "source_system": "azure_search",
                    }
                )
            return rows[:top_k]
        except Exception:  # noqa: BLE001 - Sanitize SDK/auth failures without disclosing response bodies.
            # A free semantic quota exhaustion is explicit, never upgraded to paid.
            raise ManagedStoreError("Knowledge search is temporarily unavailable") from None

    def search(self, query_embedding, top_k=6):
        return self._search("", query_embedding, top_k)

    def hybrid(self, question, query_embedding, top_k=6):
        return self._search(question, query_embedding, top_k)

    def search_keywords(self, question, top_k=6):
        return self._search(question, None, top_k)

    def search_identifiers(self, identifiers, top_k=6):
        return self._search(" ".join(identifiers), None, top_k)

    def contains_hash(self, content_hash):
        row = self.records.read("indexed_hash", content_hash)
        return bool(row)

    def count(self):
        return len(self.records.query("indexed_hash"))

    def ping(self):
        try:
            self._search("", None, 1)
            return True
        except ManagedStoreError:
            return False
