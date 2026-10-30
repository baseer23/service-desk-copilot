"""Private transactional records on SQLite; no cloud persistence clients.

Cases, voice leases, jobs and checkpoints require transactions/CAS. Knowledge
text and vectors live in Chroma; immutable source artifacts live in local files.
"""

import copy
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager

from backend.app.core.principal import current_principal
from backend.app.store.records import ManagedStateConflict, ManagedStoreError, record_id


class LocalRecords:
    is_local = True

    def __init__(self, settings):
        self.settings = settings
        self.path = settings.case_db_path
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(descriptor)
        self.active = threading.local()
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS local_records (tenant TEXT NOT NULL,"
                "id TEXT NOT NULL,kind TEXT NOT NULL,record_key TEXT NOT NULL,"
                "body TEXT NOT NULL,version INTEGER NOT NULL,updated REAL NOT NULL,"
                "PRIMARY KEY(tenant,id))"
            )
            db.execute("CREATE INDEX IF NOT EXISTS local_record_kind ON local_records(tenant,kind)")

    @contextmanager
    def connection(self):
        existing = getattr(self.active, "connection", None)
        if existing is not None:
            yield existing
            return
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            yield db
        except sqlite3.Error:
            raise ManagedStoreError("Local state could not be read or saved") from None
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        if getattr(self.active, "connection", None) is not None:
            yield self.active.connection
            return
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.active.connection = db
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                self.active.connection = None

    def tenant(self):
        return current_principal(self.settings).tenant_id

    @staticmethod
    def decode(row):
        if row is None:
            return None
        return {**json.loads(row[0]), "_etag": str(row[1])}

    def read(self, kind, key, *, tenant=None):
        with self.connection() as db:
            return self.decode(
                db.execute(
                    "SELECT body,version FROM local_records WHERE tenant=? AND id=?",
                    (tenant or self.tenant(), record_id(kind, key)),
                ).fetchone()
            )

    def write(
        self, kind, key, payload, *, previous=None, tenant=None, create_only=False, fields=None
    ):
        scope = tenant or self.tenant()
        if set(fields or {}) & {"id", "tenant_id", "kind", "key", "payload", "updated_at"}:
            raise ValueError("Invalid local record fields")
        document = {
            "id": record_id(kind, key),
            "tenant_id": scope,
            "kind": kind,
            "key": key,
            "payload": copy.deepcopy(payload),
            "updated_at": time.time(),
            **(fields or {}),
        }
        if document["tenant_id"] != scope or document["id"] != record_id(kind, key):
            raise ValueError("Invalid local record authority")
        encoded = json.dumps(document, allow_nan=False)
        if len(encoded.encode()) > 1_800_000:
            raise ManagedStoreError("Local record exceeds the safe size limit")
        with self.transaction() as db:
            current = db.execute(
                "SELECT version FROM local_records WHERE tenant=? AND id=?",
                (scope, document["id"]),
            ).fetchone()
            if (create_only and current) or (
                previous
                and (
                    previous.get("tenant_id") != scope
                    or previous.get("id") != document["id"]
                    or not current
                    or previous.get("_etag") != str(current[0])
                )
            ):
                raise ManagedStateConflict("Local state changed concurrently; retry the action")
            version = current[0] + 1 if current else 1
            db.execute(
                "INSERT INTO local_records VALUES(?,?,?,?,?,?,?) ON CONFLICT(tenant,id) "
                "DO UPDATE SET body=excluded.body,version=excluded.version,"
                "updated=excluded.updated",
                (scope, document["id"], kind, key, encoded, version, document["updated_at"]),
            )
        return {**document, "_etag": str(version)}

    def query(self, kind, *, fields=None, tenant=None, limit=1000):
        if not 1 <= limit <= 10000:
            raise ValueError("Invalid local query limit")
        clauses = ["tenant=?", "kind=?"]
        parameters = [tenant or self.tenant(), kind]
        allowed = {
            "case_id",
            "subject",
            "owner_id",
            "thread_id",
            "namespace",
            "checkpoint_id",
            "status",
            "document_id",
        }
        for name, value in (fields or {}).items():
            if name not in allowed:
                raise ValueError("Unsupported local query field")
            clauses.append("json_extract(body,?) IS ?")
            parameters.extend(["$." + name, value])
        with self.connection() as db:
            rows = db.execute(
                "SELECT body,version FROM local_records WHERE "  # noqa: S608 - Fixed clauses; every value and JSON field is bound.
                + " AND ".join(clauses)
                + " ORDER BY updated DESC LIMIT ?",
                (*parameters, min(limit, 10000)),
            ).fetchall()
        return [self.decode(row) for row in rows]

    def tenant_inventory(self, tenant: str) -> dict[str, int]:
        """Return bounded, non-content counts for an operator reset preview."""
        with self.connection() as db:
            rows = db.execute(
                "SELECT kind,COUNT(*) FROM local_records WHERE tenant=? GROUP BY kind",
                (tenant,),
            ).fetchall()
        return {str(kind): int(count) for kind, count in rows if kind != "workspace_reset"}

    def delete_tenant(self, tenant: str, *, preserve_kinds=("workspace_reset",)) -> int:
        """Delete one tenant's records while retaining the reset audit trail."""
        placeholders = ",".join("?" for _ in preserve_kinds)
        clause = f" AND kind NOT IN ({placeholders})" if preserve_kinds else ""
        with self.transaction() as db:
            cursor = db.execute(
                "DELETE FROM local_records WHERE tenant=?" + clause,
                (tenant, *preserve_kinds),
            )
            return int(cursor.rowcount)

    @contextmanager
    def locked(self, kind, key, *, tenant=None):
        scope = tenant or self.tenant()
        with self.transaction():
            previous = self.read(kind, key, tenant=scope)
            payload = copy.deepcopy(previous["payload"]) if previous else {}
            # Remote lease markers are historical metadata, not local locks.
            payload.update(lock_owner="", lock_until=0)
            yield payload
            self.write(
                kind, key, payload, previous=previous, tenant=scope, create_only=previous is None
            )

    def atomic_batch(self, operations, *, tenant):
        with self.transaction():
            for operation, arguments, options in operations:
                document = arguments[-1]
                if document["tenant_id"] != tenant:
                    raise PermissionError("Local batch tenant mismatch")
                previous = self.read(document["kind"], document["key"], tenant=tenant)
                if operation == "replace" and (
                    not previous or previous["_etag"] != options.get("if_match_etag")
                ):
                    raise ManagedStateConflict("Local batch changed concurrently")
                if operation not in {"create", "replace"}:
                    raise ValueError("Unsupported local batch operation")
                self.write(
                    document["kind"],
                    document["key"],
                    document["payload"],
                    previous=previous if operation == "replace" else None,
                    create_only=operation == "create",
                    tenant=tenant,
                    fields={
                        k: v
                        for k, v in document.items()
                        if k
                        not in {
                            "id",
                            "tenant_id",
                            "kind",
                            "key",
                            "payload",
                            "updated_at",
                        }
                        and not k.startswith("_")
                    },
                )

    def close(self):
        pass  # Each operation owns and closes its connection.

    def import_record(self, document):
        """Copy a backed-up record without changing its payload, authority or timestamps."""
        clean = {key: value for key, value in document.items() if not key.startswith("_")}
        if clean["id"] != record_id(clean["kind"], clean["key"]):
            raise ValueError("Invalid imported record identity")
        encoded = json.dumps(clean, sort_keys=True, allow_nan=False)
        with self.transaction() as db:
            previous = db.execute(
                "SELECT body FROM local_records WHERE tenant=? AND id=?",
                (clean["tenant_id"], clean["id"]),
            ).fetchone()
            if previous:
                if json.loads(previous[0]) != clean:
                    raise ManagedStoreError("Imported record conflicts with existing local data")
                return False
            db.execute(
                "INSERT INTO local_records VALUES(?,?,?,?,?,1,?)",
                (
                    clean["tenant_id"],
                    clean["id"],
                    clean["kind"],
                    clean["key"],
                    encoded,
                    clean.get("updated_at", 0),
                ),
            )
        reread = self.read(clean["kind"], clean["key"], tenant=clean["tenant_id"])
        if {key: value for key, value in reread.items() if not key.startswith("_")} != clean:
            raise ManagedStoreError("Imported record verification failed")
        return True


def record_store(settings):
    """Application records always persist on the local filesystem."""
    return LocalRecords(settings)
