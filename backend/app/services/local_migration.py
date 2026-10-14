"""Offline, additive migration from a verified export. Never contacts/deletes Azure."""

import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from pathlib import Path

from backend.app.core.principal import Principal, principal_scope
from backend.app.services.azure_migration import RecordMigration, digest
from backend.app.services.capabilities import knowledge_stores
from backend.app.store.local_records import LocalRecords


def file_digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def verify_backup(root):
    manifest = json.loads((root / "manifest.json").read_text())
    if not manifest.get("verified") or not manifest.get("stable_records"):
        raise ValueError("A verified, stable backup is required")
    for name, entry in manifest["files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError("Invalid backup file")
        if path.stat().st_size != entry["bytes"] or file_digest(path) != entry["sha256"]:
            raise ValueError("Backup integrity check failed")
    return manifest


def rows(path):
    with path.open() as source:
        return [json.loads(line) for line in source if line.strip()]


def artifact_path(name):
    relative = Path(name)
    if relative.name not in {"original", "extraction.json", "vectors.json"}:
        return None
    if (
        relative.is_absolute()
        or len(relative.parts) != 3
        or not all(re.fullmatch(r"[a-f0-9]{64}", part) for part in relative.parts[:2])
    ):
        raise ValueError("Unsafe document artifact path")
    return relative


def legacy_vectors(root):
    """Read scratch copies: opening Chroma must never mutate a verified backup."""
    import chromadb
    from chromadb.config import Settings

    exported = []
    for relative in ("previous-local/chroma", "earlier-local/chroma"):
        path = root / relative
        if not path.exists():
            continue
        with tempfile.TemporaryDirectory(prefix="deskmate-vector-read-") as temporary:
            copied = Path(temporary) / "chroma"
            shutil.copytree(path, copied)
            client = chromadb.PersistentClient(
                path=str(copied), settings=Settings(anonymized_telemetry=False)
            )
            for collection in client.list_collections():
                values = collection.get(include=["documents", "metadatas", "embeddings"])
                for i, identifier in enumerate(values["ids"]):
                    exported.append(
                        {
                            "collection": collection.name,
                            "id": identifier,
                            "text": values["documents"][i],
                            "metadata": values["metadatas"][i] or {},
                            "embedding": list(values["embeddings"][i]),
                        }
                    )
    return exported


def cloud_chunk(chunk, root, originals, settings):
    metadata = json.loads(chunk["metadata_json"])
    vector = chunk.get("vector")
    artifact = (
        root
        / "blobs"
        / hashlib.sha256(chunk["tenant_id"].encode()).hexdigest()
        / metadata["doc_id"]
        / "vectors.json"
    )
    if vector is None and artifact.exists():
        values = json.loads(artifact.read_text())
        ordinal = int(chunk["chunk_id"].removeprefix(metadata["doc_id"] + "-"))
        vector = values[ordinal]
    if vector is None:
        candidates = [
            row["embedding"]
            for row in originals
            if row["text"] == chunk["text"]
            and row["metadata"].get("content_hash") == metadata.get("content_hash")
            and row["metadata"].get("embedding_provider") == metadata.get("embedding_provider")
            and row["metadata"].get("embedding_model") == metadata.get("embedding_model")
        ]
        if not candidates or any(value != candidates[0] for value in candidates):
            raise ValueError("An original knowledge vector could not be recovered unambiguously")
        vector = candidates[0]
    if len(vector) != settings.azure_embedding_dimensions:
        raise ValueError("Recovered vector dimensions do not match")
    return {
        "id": chunk["chunk_id"],
        "text": chunk["text"],
        "metadata": metadata,
        "embedding": vector,
    }


def import_legacy_database(path, records, tenant, report):
    migration = RecordMigration(records, file_digest(path)[:16])
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        source_tag = file_digest(path)[:16]

        def copy(kind, key, payload, fields=None, scope=tenant):
            previous = records.read(kind, key, tenant=scope)
            if previous and digest(previous["payload"]) != digest(payload):
                # Keep the active cloud version and every older differing version.
                kind, key = "legacy_" + kind, source_tag + ":" + key
            return migration.copy(kind, key, payload, fields, tenant=scope)

        if "cases" in tables:
            for row in db.execute("SELECT * FROM cases"):
                report["legacy_cases"] += copy(
                    "case", row["case_id"], json.loads(row["state_json"])
                )
        if "facts" in tables:
            for row in db.execute("SELECT * FROM facts"):
                payload = dict(row)
                payload["metadata"] = json.loads(payload.pop("metadata_json"))
                report["legacy_facts"] += copy(
                    "fact",
                    source_tag + ":" + str(row["id"]),
                    payload,
                    {"case_id": row["case_id"], "subject": row["subject"]},
                )
        if "events" in tables:
            for row in db.execute("SELECT * FROM events"):
                payload = {
                    key: row[key] for key in ("id", "tool", "source", "content", "created_at")
                }
                report["legacy_events"] += copy(
                    "event",
                    row["case_id"] + ":" + source_tag + ":" + str(row["id"]),
                    payload,
                    {"case_id": row["case_id"]},
                )
        if "voice_sessions" in tables:
            for row in db.execute("SELECT * FROM voice_sessions"):
                state = json.loads(row["state_json"])
                events = [
                    dict(event)
                    for event in db.execute(
                        "SELECT * FROM voice_session_events WHERE session_id=?",
                        (row["session_id"],),
                    )
                ]
                payload = {
                    "state": state,
                    "lease_id": row["lease_id"] or "",
                    "lease_until": row["lease_until"],
                    "events": events,
                }
                scope = row["tenant_id"]  # Never remap transcript ownership.
                report["legacy_voice"] += copy(
                    "voice", row["session_id"], payload, {"owner_id": row["owner_id"]}, scope
                )
                if not records.read("voice_owner", row["owner_id"], tenant=scope):
                    records.write(
                        "voice_owner",
                        row["owner_id"],
                        {"session_id": row["session_id"]},
                        tenant=scope,
                    )
        if "knowledge_documents" in tables:
            from backend.app.services.local_documents import LocalDocumentRepository

            repository = LocalDocumentRepository(records.settings, records=records)
            for row in db.execute("SELECT * FROM knowledge_documents"):
                result = repository.upload(row["text"].encode(), row["title"], "text/plain")
                report["legacy_documents"] += not result["duplicate"]
                if not result["duplicate"]:
                    document = records.read("document", result["id"])
                    # An old local document is not permission to submit it to AI.
                    records.write(
                        "document",
                        result["id"],
                        {**document["payload"], "status": "saved"},
                        previous=document,
                        fields={"status": "saved"},
                    )
                # Extracted text is retained immediately without any paid operation.
                from backend.app.services.chunking import split_text

                vector, _, _ = knowledge_stores(
                    records.settings, connect_graph=False, records=records
                )
                vector.upsert(
                    [
                        {
                            "id": result["id"] + "-" + str(chunk["ord"]),
                            "text": chunk["text"],
                            "embedding": None,
                            "metadata": {
                                "doc_id": result["id"],
                                "title": row["title"],
                                "source_type": row["source_type"],
                                "content_hash": result["id"],
                            },
                        }
                        for chunk in split_text(row["text"])
                    ]
                )
        if "azure_usage" in tables:
            current = records.read("usage_policy", "subscription", tenant="system:usage")
            cloud = {row["id"]: row for row in current["payload"]["tables"]["azure_usage"]}
            for row in db.execute("SELECT * FROM azure_usage"):
                if row["id"] not in cloud or dict(row) != cloud[row["id"]]:
                    raise ValueError("An older financial ledger contains unreconciled history")
        # The immutable complete database snapshot also preserves auxiliary tables.


async def import_legacy_checkpoints(path, records, report):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from backend.app.agent.azure_checkpoints import CosmosCheckpointSaver

    saver = CosmosCheckpointSaver(records)
    # SqliteSaver may initialize schemas: use a private scratch copy.
    with tempfile.TemporaryDirectory(prefix="deskmate-checkpoint-read-") as temporary:
        copied = Path(temporary) / "checkpoints.db"
        shutil.copy2(path, copied)
        async with AsyncSqliteSaver.from_conn_string(str(copied)) as source:
            async for item in source.alist(None):
                previous = await saver.aget_tuple(item.config)
                if previous:
                    if previous.checkpoint != item.checkpoint:
                        raise ValueError("Legacy checkpoint conflicts with the active history")
                    continue
                await saver.aput(
                    item.parent_config
                    or {
                        "configurable": {
                            key: value
                            for key, value in item.config["configurable"].items()
                            if key != "checkpoint_id"
                        }
                    },
                    item.checkpoint,
                    item.metadata,
                    {},
                )
                pending = {}
                for task, channel, value in item.pending_writes or []:
                    pending.setdefault(task, []).append((channel, value))
                for task, writes in pending.items():
                    await saver.aput_writes(item.config, writes, task)
                reread = await saver.aget_tuple(item.config)
                if reread.checkpoint != item.checkpoint or sorted(
                    map(str, reread.pending_writes)
                ) != sorted(map(str, item.pending_writes or [])):
                    raise ValueError("Legacy checkpoint verification failed")
                report["legacy_checkpoints"] += 1


def migrate(root, settings):
    os.umask(0o077)
    root = root.resolve()
    verify_backup(root)
    if (
        settings.case_db_path.exists()
        or settings.chroma_dir.exists()
        or settings.local_documents_dir.exists()
    ):
        raise ValueError("Use fresh local destinations; existing stores are never overwritten")
    records = LocalRecords(settings)
    report = Counter()
    cloud_records = rows(root / "records.jsonl")
    for row in cloud_records:
        records.import_record(row)
        report["cloud_" + row["kind"]] += 1
    originals = legacy_vectors(root)
    vector, _, _ = knowledge_stores(settings, connect_graph=False, records=records)
    for row in rows(root / "search.jsonl"):
        with principal_scope(Principal(row["tenant_id"], "local-migration", True)):
            chunk = cloud_chunk(row, root, originals, settings)
            vector.upsert([chunk])
            values = vector.vector._collection.get(
                ids=[vector.physical_id(chunk["id"])],
                include=["documents", "metadatas", "embeddings"],
            )
            if (
                values["documents"] != [chunk["text"]]
                or json.loads(values["metadatas"][0]["metadata_json"]) != chunk["metadata"]
            ):
                raise ValueError("Local Chroma text/metadata verification failed")
            # Chroma stores float32; compare exactly with the float32 source representation.
            import numpy as np

            if not np.array_equal(
                np.asarray(values["embeddings"][0], dtype=np.float32),
                np.asarray(chunk["embedding"], dtype=np.float32),
            ):
                raise ValueError("Local Chroma vector verification failed")
            report["cloud_vectors"] += 1
    for entry in rows(root / "blobs.jsonl"):
        relative = artifact_path(entry["name"])
        if relative is None:
            continue  # Deployment packages remain in the verified backup.
        target = settings.local_documents_dir / relative
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        shutil.copy2(root / "blobs" / relative, target)
        target.chmod(0o600)
        if file_digest(target) != entry["sha256"]:
            raise ValueError("Local original/artifact verification failed")
        report["document_artifacts"] += 1
    tenant = (
        settings.auth_allowed_tenants[0]
        if settings.auth_allowed_tenants
        else settings.voice_tenant_id
    )
    with principal_scope(Principal(tenant, "local-migration", True)):
        for row in originals:
            name = "archive_" + hashlib.sha256(row["collection"].encode()).hexdigest()[:16]
            archive = vector.vector._client.get_or_create_collection(
                name=name,
                embedding_function=None,
                metadata={"source_collection": row["collection"], "semantic_queries": "disabled"},
            )
            identifier = vector.physical_id(row["collection"] + ":" + row["id"])
            archive.upsert(
                ids=[identifier],
                documents=[row["text"]],
                embeddings=[row["embedding"]],
                metadatas=[
                    {
                        "tenant_id": tenant,
                        "chunk_id": row["id"],
                        "metadata_json": json.dumps(row["metadata"]),
                    }
                ],
            )
            if archive.get(ids=[identifier])["documents"] != [row["text"]]:
                raise ValueError("Legacy Chroma archive verification failed")
            report["archived_vectors"] += 1
        for relative in ("previous-local", "earlier-local"):
            for path in sorted((root / relative).glob("*")):
                if path.suffix not in {".db", ".sqlite3"}:
                    continue
                with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                    tables = {
                        row[0]
                        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    }
                if "checkpoints" in tables:
                    asyncio.run(import_legacy_checkpoints(path, records, report))
                else:
                    import_legacy_database(path, records, tenant, report)
    # Recheck cloud records after all imports; financial history must be byte-equivalent.
    for row in cloud_records:
        current = records.read(row["kind"], row["key"], tenant=row["tenant_id"])
        if digest(current["payload"]) != digest(row["payload"]):
            raise ValueError("Migrated cloud payload changed during import")
    verify_backup(root)
    result = {
        "verified": True,
        "counts": dict(report),
        "source_backup": str(root),
        "backup_manifest_sha256": file_digest(root / "manifest.json"),
        "cloud_mutations": False,
        "model_calls": 0,
        "financial_history_preserved": True,
    }
    report_path = settings.case_db_path.parent / "migration-report.private.json"
    report_path.write_text(json.dumps(result, indent=2))
    report_path.chmod(0o600)
    return result
