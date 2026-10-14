"""Tenant-partitioned Cosmos records with Entra identity and conditional writes."""

import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from typing import Any

from backend.app.core.config import Settings
from backend.app.core.principal import current_principal


class ManagedStoreError(RuntimeError):
    pass


def azure_credential(settings: Settings):
    from azure.identity import AzureCliCredential, DefaultAzureCredential

    return (
        AzureCliCredential(process_timeout=5)
        if settings.deployment_mode == "local"
        else DefaultAzureCredential(
            exclude_interactive_browser_credential=True,
            process_timeout=5,
            connection_timeout=5,
            read_timeout=5,
            retry_total=0,
        )
    )


def record_id(kind: str, key: str) -> str:
    return kind + "-" + hashlib.sha256(key.encode()).hexdigest()


def connection_policy():
    # Cosmos 4.17 treats retry_total=0 as falsy for its separate 429 retry loop.
    # An explicit policy also prevents hidden throttle retries of leased writes.
    from azure.cosmos.documents import ConnectionPolicy, RetryOptions

    policy = ConnectionPolicy()
    policy.RetryOptions = RetryOptions(max_retry_attempt_count=0, max_wait_time_in_seconds=0)
    return policy


class CosmosRecords:
    def __init__(self, settings: Settings, container: Any = None):
        self.settings = settings
        self.credential = None
        self.client = None
        if container is None:
            from azure.cosmos import CosmosClient

            if not settings.azure_cosmos_endpoint:
                raise ManagedStoreError("Managed database is not configured")
            self.credential = azure_credential(settings)
            self.client = CosmosClient(
                settings.azure_cosmos_endpoint,
                credential=self.credential,
                retry_total=0,
                connection_policy=connection_policy(),
                connection_timeout=5,
                read_timeout=10,
            )
            container = self.client.get_database_client(
                settings.azure_cosmos_database
            ).get_container_client(settings.azure_cosmos_container)
        self.container = container

    def tenant(self) -> str:
        return current_principal(self.settings).tenant_id

    def read(self, kind: str, key: str, *, tenant: str | None = None) -> dict | None:
        try:
            scope = tenant or self.tenant()
            row = self.container.read_item(record_id(kind, key), partition_key=scope)
            if row.get("tenant_id") != scope:
                raise ManagedStoreError("Managed tenant isolation check failed")
            return row
        except Exception as error:  # noqa: BLE001 - Sanitize SDK/auth failures without disclosing response bodies.
            if getattr(error, "status_code", None) == 404:
                return None
            raise ManagedStoreError("Managed state could not be read") from None

    def write(
        self,
        kind: str,
        key: str,
        payload: dict,
        *,
        previous: dict | None = None,
        tenant: str | None = None,
        create_only: bool = False,
        fields: dict | None = None,
    ) -> dict:
        from azure.core import MatchConditions

        document = {
            "id": record_id(kind, key),
            "tenant_id": tenant or self.tenant(),
            "kind": kind,
            "key": key,
            "payload": payload,
            "updated_at": time.time(),
            **(fields or {}),
        }
        if len(json.dumps(document, default=str).encode()) > 1_800_000:
            raise ManagedStoreError("Managed record exceeds the safe size limit")
        if not document["tenant_id"].startswith("system:") and kind != "record_quota":
            with self.locked("record_quota", "tenant", tenant=document["tenant_id"]) as quota:
                sizes = quota.setdefault("objects", {})
                size = len(json.dumps(document, default=str).encode())
                total = sum(sizes.values()) - sizes.get(document["id"], 0) + size
                if total > 100_000_000 or (document["id"] not in sizes and len(sizes) >= 5000):
                    raise ManagedStoreError("Managed data allowance is full")
                sizes[document["id"]] = max(size, sizes.get(document["id"], 0))
        try:
            if previous:
                return self.container.replace_item(
                    previous["id"],
                    document,
                    etag=previous["_etag"],
                    match_condition=MatchConditions.IfNotModified,
                )
            if create_only:
                return self.container.create_item(document)
            return self.container.upsert_item(document)
        except Exception as error:  # noqa: BLE001 - Sanitize SDK/auth failures without disclosing response bodies.
            if getattr(error, "status_code", None) in {409, 412}:
                raise ManagedStoreError(
                    "Managed state changed concurrently; retry the operation"
                ) from None
            raise ManagedStoreError("Managed state could not be saved") from None

    def query(
        self, kind: str, *, fields: dict | None = None, tenant: str | None = None, limit: int = 1000
    ) -> list[dict]:
        clauses, parameters = ["c.kind=@kind"], [{"name": "@kind", "value": kind}]
        for index, (name, value) in enumerate((fields or {}).items()):
            if name not in {
                "case_id",
                "subject",
                "owner_id",
                "thread_id",
                "namespace",
                "checkpoint_id",
                "status",
                "document_id",
            }:
                raise ValueError("Unsupported managed query field")
            clauses.append(f"c.{name}=@p{index}")
            parameters.append({"name": f"@p{index}", "value": value})
        try:
            rows = self.container.query_items(
                query="SELECT * FROM c WHERE " + " AND ".join(clauses),  # noqa: S608 - Identifiers come from a fixed validated allowlist.
                parameters=parameters,
                partition_key=tenant or self.tenant(),
                max_item_count=min(limit, 100),
            )
            results = []
            for row in rows:
                if row.get("tenant_id") != (tenant or self.tenant()):
                    raise ManagedStoreError("Managed tenant isolation check failed")
                results.append(row)
                if len(results) >= limit:
                    break
            return sorted(results, key=lambda row: row["updated_at"], reverse=True)
        except Exception:  # noqa: BLE001 - Sanitize SDK/auth failures without disclosing response bodies.
            raise ManagedStoreError("Managed state query failed") from None

    @contextmanager
    def locked(self, kind: str, key: str, *, tenant: str | None = None):
        """Short distributed lease; stale owners cannot commit after a takeover."""
        owner = uuid.uuid4().hex
        scope = tenant or self.tenant()
        row = None
        for _ in range(5):
            existing = self.read(kind, key, tenant=scope)
            payload = existing["payload"] if existing else {}
            if payload.get("lock_until", 0) > time.time():
                time.sleep(0.05)
                continue
            try:
                row = self.write(
                    kind,
                    key,
                    {**payload, "lock_owner": owner, "lock_until": time.time() + 15},
                    previous=existing,
                    tenant=scope,
                    create_only=existing is None,
                )
                break
            except ManagedStoreError:
                time.sleep(0.05)
        if row is None:
            raise ManagedStoreError("Managed state is busy; retry shortly")
        payload = dict(row["payload"])
        try:
            yield payload
            if payload["lock_owner"] != owner or payload["lock_until"] <= time.time():
                raise ManagedStoreError("Managed state lease expired")
            payload.update(lock_owner="", lock_until=0)
            self.write(kind, key, payload, previous=row, tenant=scope)
        except BaseException:
            # No write on failure: the previous conservative reservation remains.
            # The short lease expires automatically rather than unlocking a newer owner.
            raise

    def close(self):
        if self.client:
            self.client.close()
        if self.credential:
            self.credential.close()
