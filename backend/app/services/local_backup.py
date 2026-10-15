"""Non-destructive local snapshots and integrity-checked transactional record migration."""

import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from backend.app.store.records import ManagedStoreError


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
    if target.exists():
        raise ValueError("Use a fresh SQLite snapshot target")
    if not source.is_file():
        raise ValueError("SQLite snapshot source does not exist")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(target)) as backup:
            original.backup(backup)
            backup.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            backup.execute("PRAGMA journal_mode=DELETE")
            if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Backup integrity check failed")
    target.chmod(0o600)
    return {"file": target.name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}


def snapshot_sources(settings, output: Path):
    if output.exists():
        raise ValueError("Use a fresh private snapshot directory")
    output.mkdir(mode=0o700, parents=True)
    manifest = {"version": 2, "sqlite": [], "chroma": [], "documents": []}
    candidates = [
        settings.case_db_path,
        Path(str(settings.case_db_path) + ".supervisor.checkpoints.sqlite3"),
    ]
    visited = set()
    for source in candidates:
        if source and source.exists() and source.resolve() not in visited:
            visited.add(source.resolve())
            destination = output / source.name
            if destination.exists():
                raise ValueError("Local SQLite sources have colliding backup names")
            manifest["sqlite"].append(snapshot_sqlite(source.resolve(), destination))
    if settings.chroma_dir.exists():
        # Stop ingestion before this copy; verify the committed Chroma SQLite state.
        shutil.copytree(settings.chroma_dir, output / "chroma", symlinks=True)
        for file in (output / "chroma").rglob("*"):
            if file.is_symlink():
                raise ValueError("A Chroma backup must not contain symbolic links")
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
    if settings.local_documents_dir.exists():
        shutil.copytree(settings.local_documents_dir, output / "documents", symlinks=True)
        for file in (output / "documents").rglob("*"):
            if file.is_symlink():
                raise ValueError("A document backup must not contain symbolic links")
            if file.is_file():
                file.chmod(0o600)
                manifest["documents"].append({
                    "file": str(file.relative_to(output)), "bytes": file.stat().st_size,
                    "sha256": file_sha256(file),
                })
            elif file.is_dir():
                file.chmod(0o700)
    (output / "backup-manifest.json").write_text(json.dumps(manifest, indent=2))
    (output / "backup-manifest.json").chmod(0o600)
    return manifest


def file_sha256(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def verify_local_snapshot(root):
    """Rehash a current or legacy local snapshot without opening its databases."""
    root = root.resolve()
    manifest = json.loads((root / "backup-manifest.json").read_text())
    checked = 0
    for kind in ("sqlite", "chroma", "documents"):
        for entry in manifest.get(kind, []):
            relative = Path(entry["file"])
            candidate = root / relative
            path = candidate.resolve()
            if (
                relative.is_absolute() or ".." in relative.parts
                or candidate.is_symlink() or candidate != path
                or not path.is_relative_to(root) or not path.is_file()
            ):
                raise ValueError("Invalid local snapshot file")
            if file_sha256(path) != entry["sha256"] or (
                "bytes" in entry and path.stat().st_size != entry["bytes"]
            ):
                raise ValueError("Local snapshot integrity verification failed")
            checked += 1
    return {"verified": True, "files_verified": checked, "manifest_version": manifest.get("version", 1)}


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
                raise ManagedStoreError("Migration conflicts with an existing local record")
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
            raise ManagedStoreError("Local migration integrity verification failed")
        return True

    def rollback_records(self, manifest):
        """Only remove unchanged records copied by this local migration."""
        removed, retained = 0, 0
        with self.records.transaction() as db:
            for item in reversed(manifest):
                if item["kind"] == "usage_policy":
                    retained += 1
                    continue  # Financial history and shutdown policy are never rolled back.
                row = self.records.read(item["kind"], item["key"], tenant=item["tenant"])
                if not row:
                    continue
                if row["_etag"] != item["etag"] or digest(row["payload"]) != item["sha256"]:
                    retained += 1
                    continue
                cursor = db.execute(
                    "DELETE FROM local_records WHERE tenant=? AND id=? AND version=?",
                    (item["tenant"], row["id"], int(row["_etag"])),
                )
                removed += cursor.rowcount
        return {"removed": removed, "retained_changed_records": retained}
