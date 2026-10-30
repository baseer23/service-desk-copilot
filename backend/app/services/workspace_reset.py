"""Fail-closed, tenant-scoped operator workspace reset orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import shutil
import time
from pathlib import Path

from backend.app.store.local_records import LocalRecords

_blocked: set[str] = set()
_locks: dict[str, asyncio.Lock] = {}


def workspace_blocked(tenant_id: str) -> bool:
    return tenant_id in _blocked


class WorkspaceResetService:
    def __init__(self, settings):
        self.settings = settings
        self.records = LocalRecords(settings)

    @staticmethod
    def _token_key(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def _chroma_inventory(self, tenant_id: str) -> tuple[int, int]:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        client = chromadb.PersistentClient(
            path=str(self.settings.chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        owned = unresolved = 0
        for summary in client.list_collections():
            collection = client.get_collection(summary.name, embedding_function=None)
            rows = collection.get(include=["metadatas"])
            for metadata in rows.get("metadatas") or []:
                metadata = metadata or {}
                embedded = {}
                try:
                    embedded = json.loads(metadata.get("metadata_json", "{}"))
                except (TypeError, ValueError):
                    pass
                owner = metadata.get("tenant_id") or embedded.get("tenant_id")
                if owner == tenant_id:
                    owned += 1
                elif not owner:
                    unresolved += 1
        return owned, unresolved

    def inventory(self, tenant_id: str) -> dict:
        record_counts = self.records.tenant_inventory(tenant_id)
        try:
            vectors, unresolved_vectors = self._chroma_inventory(tenant_id)
        except Exception:
            vectors, unresolved_vectors = 0, 0
            chroma_status = "unavailable"
        else:
            chroma_status = "ready"
        tenant_dir = Path(self.settings.local_documents_dir) / hashlib.sha256(
            tenant_id.encode()
        ).hexdigest()
        files = sum(1 for path in tenant_dir.rglob("*") if path.is_file()) if tenant_dir.exists() else 0
        return {
            "records": sum(record_counts.values()),
            "records_by_kind": record_counts,
            "vectors": vectors,
            "files": files,
            "unresolved_vectors": unresolved_vectors,
            "stores": {"sqlite": "ready", "chroma": chroma_status},
        }

    def prepare(self, tenant_id: str, owner_id: str) -> dict:
        token = secrets.token_urlsafe(32)
        expires_at = time.time() + 300
        inventory = self.inventory(tenant_id)
        self.records.write(
            "workspace_reset", self._token_key(token),
            {"owner_id": owner_id, "expires_at": expires_at, "used": False, "status": "prepared"},
            tenant=tenant_id, create_only=True,
        )
        return {"confirmation_token": token, "expires_at": expires_at, "inventory": inventory}

    def _delete_chroma(self, tenant_id: str) -> dict:
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        client = chromadb.PersistentClient(
            path=str(self.settings.chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        deleted = unresolved = 0
        for summary in client.list_collections():
            collection = client.get_collection(summary.name, embedding_function=None)
            rows = collection.get(include=["metadatas"])
            selected = []
            for identifier, metadata in zip(rows.get("ids") or [], rows.get("metadatas") or []):
                metadata = metadata or {}
                embedded = {}
                try:
                    embedded = json.loads(metadata.get("metadata_json", "{}"))
                except (TypeError, ValueError):
                    pass
                owner = metadata.get("tenant_id") or embedded.get("tenant_id")
                if owner == tenant_id:
                    selected.append(identifier)
                elif not owner:
                    unresolved += 1
            if selected:
                collection.delete(ids=selected)
                deleted += len(selected)
        return {"status": "cleared", "deleted": deleted, "unresolved": unresolved}

    def _delete_graph(self, tenant_id: str) -> dict:
        if not self.settings.neo4j_uri or not self.settings.neo4j_ingestion_enabled:
            return {"status": "not_configured", "deleted": 0}
        from neo4j import GraphDatabase

        password = self.settings.neo4j_ingestion_password.get_secret_value()
        if not self.settings.neo4j_ingestion_user or not password:
            return {"status": "unavailable", "deleted": 0}
        driver = GraphDatabase.driver(
            self.settings.neo4j_uri,
            auth=(self.settings.neo4j_ingestion_user, password),
        )
        try:
            from backend.app.store.graph_repo import GraphRepository

            return GraphRepository(
                driver,
                tenant_provider=lambda: tenant_id,
                database=self.settings.neo4j_database,
            ).delete_workspace_graph(tenant_id)
        finally:
            driver.close()

    async def confirm(self, tenant_id: str, owner_id: str, token: str) -> dict:
        key = self._token_key(token)
        lock = _locks.setdefault(tenant_id, asyncio.Lock())
        async with lock:
            record = self.records.read("workspace_reset", key, tenant=tenant_id)
            payload = record["payload"] if record else {}
            if (
                not record or (payload.get("used") and payload.get("status") != "incomplete") or payload.get("owner_id") != owner_id
                or payload.get("expires_at", 0) < time.time()
            ):
                raise ValueError("Reset confirmation is invalid, expired, or already used")
            payload.update(used=True, status="running")
            self.records.write("workspace_reset", key, payload, previous=record, tenant=tenant_id)
            _blocked.add(tenant_id)
            stores: dict[str, dict] = {}
            try:
                try:
                    stores["neo4j"] = await asyncio.to_thread(self._delete_graph, tenant_id)
                except Exception:
                    stores["neo4j"] = {"status": "failed", "deleted": 0}
                try:
                    stores["chroma"] = await asyncio.to_thread(self._delete_chroma, tenant_id)
                except Exception:
                    stores["chroma"] = {"status": "failed", "deleted": 0}
                root = Path(self.settings.local_documents_dir).resolve()
                tenant_dir = (root / hashlib.sha256(tenant_id.encode()).hexdigest()).resolve()
                if tenant_dir != root and tenant_dir.is_relative_to(root) and tenant_dir.exists():
                    await asyncio.to_thread(shutil.rmtree, tenant_dir)
                stores["files"] = {"status": "cleared", "deleted": True}
                deleted_records = await asyncio.to_thread(self.records.delete_tenant, tenant_id)
                stores["sqlite"] = {"status": "cleared", "deleted": deleted_records}
                incomplete = any(
                    value["status"] in {"failed", "incomplete", "unavailable"}
                    or value.get("unresolved", 0)
                    for value in stores.values()
                )
                result = {"status": "incomplete" if incomplete else "complete", "stores": stores}
                audit = self.records.read("workspace_reset", key, tenant=tenant_id)
                if audit:
                    self.records.write(
                        "workspace_reset", key, {**audit["payload"], **result},
                        previous=audit, tenant=tenant_id,
                    )
                return result
            finally:
                _blocked.discard(tenant_id)


# Backward-compatible short name for callers while the service remains internal.
WorkspaceReset = WorkspaceResetService
