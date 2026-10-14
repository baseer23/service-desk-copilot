"""Non-destructive local snapshots and integrity-checked managed-record migration."""

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from backend.app.store.azure_records import ManagedStoreError


def digest(payload):
    def canonical(value):
        if isinstance(value, dict):
            return {key: canonical(item) for key, item in value.items()}
        if isinstance(value, list):
            return [canonical(item) for item in value]
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value

    return hashlib.sha256(
        json.dumps(
            canonical(payload), sort_keys=True, separators=(",", ":"), default=str, allow_nan=False
        ).encode()
    ).hexdigest()


def snapshot_sqlite(source: Path, target: Path):
    """SQLite online backup includes committed WAL state without modifying the source."""
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
        with sqlite3.connect(target) as backup:
            original.backup(backup)
            if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")
    target.chmod(0o600)
    return {"file": target.name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


def snapshot_sources(settings, output: Path):
    if output.exists():
        raise ValueError("Use a fresh private snapshot directory")
    output.mkdir(mode=0o700, parents=True)
    manifest = {"sqlite": [], "chroma": []}
    candidates = [
        settings.case_db_path,
        Path(str(settings.case_db_path) + ".supervisor.checkpoints.sqlite3"),
        settings.azure_usage_db_path,
    ]
    for source in candidates:
        if source and source.exists():
            manifest["sqlite"].append(snapshot_sqlite(source.resolve(), output / source.name))
    if settings.chroma_dir.exists():
        # Stop ingestion before this copy; verify the committed Chroma SQLite state.
        shutil.copytree(settings.chroma_dir, output / "chroma")
        for file in (output / "chroma").rglob("*"):
            if file.is_file():
                file.chmod(0o600)
                manifest["chroma"].append(
                    {
                        "file": str(file.relative_to(output)),
                        "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                    }
                )
            elif file.is_dir():
                file.chmod(0o700)
    (output / "backup-manifest.json").write_text(json.dumps(manifest, indent=2))
    (output / "backup-manifest.json").chmod(0o600)
    return manifest


class RecordMigration:
    def __init__(self, records, migration_id):
        self.records = records
        self.migration_id = migration_id
        self.manifest = []

    def copy(self, kind, key, payload, fields=None, tenant=None):
        previous = self.records.read(kind, key, tenant=tenant)
        expected = digest(payload)
        if previous:
            if digest(previous["payload"]) != expected:
                raise ManagedStoreError("Migration conflicts with an existing managed record")
            return False
        saved = self.records.write(
            kind, key, payload, fields=fields, tenant=tenant, create_only=True
        )
        self.manifest.append(
            {
                "kind": kind,
                "key": key,
                "tenant": saved["tenant_id"],
                "id": saved["id"],
                "sha256": expected,
                "etag": saved["_etag"],
            }
        )
        reread = self.records.read(kind, key, tenant=tenant)
        if not reread or digest(reread["payload"]) != expected:
            raise ManagedStoreError("Managed migration integrity verification failed")
        return True

    def rollback_records(self, manifest):
        """Explicit operator operation: leave any subsequently changed record intact."""
        from azure.core import MatchConditions

        removed, retained = 0, 0
        for item in reversed(manifest):
            if item["kind"] == "usage_policy":
                retained += 1
                continue  # Financial history and persistent shutdown are never rolled back.
            row = self.records.read(item["kind"], item["key"], tenant=item["tenant"])
            if not row:
                continue
            if row["_etag"] != item["etag"] or digest(row["payload"]) != item["sha256"]:
                retained += 1
                continue
            self.records.container.delete_item(
                row["id"],
                partition_key=item["tenant"],
                etag=row["_etag"],
                match_condition=MatchConditions.IfNotModified,
            )
            removed += 1
        return {"removed": removed, "retained_changed_records": retained}
