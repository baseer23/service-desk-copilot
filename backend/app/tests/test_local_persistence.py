"""Real local SQLite/Chroma, process restart, transactions and immutable originals."""

import gc
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from backend.app.core.azure_usage import AzureUsageBlocked, AzureUsageGuard
from backend.app.core.principal import Principal, principal_scope
from backend.app.services.azure_migration import snapshot_sqlite
from backend.app.services.capabilities import knowledge_stores
from backend.app.services.local_documents import LocalDocumentRepository
from backend.app.store.azure_records import ManagedStateConflict, ManagedStoreError
from backend.app.store.local_knowledge import LocalDocuments
from backend.app.store.local_records import LocalRecords


def config(settings, **updates):
    return settings.model_copy(
        update={
            "local_persistence_enabled": True,
            "local_documents_dir": settings.case_db_path.parent / "originals",
            **updates,
        }
    )


def test_import_is_lossless_tenant_scoped_and_conflicts_preserve_original(settings):
    records = LocalRecords(config(settings))
    source = {
        "id": "case:legacy",
        "kind": "case",
        "key": "legacy",
        "tenant_id": "a",
        "payload": {"transcript": ["Fictional preserved conversation"]},
        "updated_at": 123,
        "_etag": "cloud-etag",
    }
    # Use the actual canonical identity, which hashes the key.
    from backend.app.store.azure_records import record_id

    source["id"] = record_id("case", "legacy")
    assert records.import_record(source)
    assert not records.import_record(source)
    assert records.read("case", "legacy", tenant="b") is None
    restarted = LocalRecords(config(settings))
    assert restarted.read("case", "legacy", tenant="a")["payload"] == source["payload"]
    with pytest.raises(ManagedStoreError, match="conflicts"):
        restarted.import_record({**source, "payload": {"replacement": True}})


def test_cas_race_and_nested_transaction_rollback(settings):
    records = LocalRecords(config(settings))
    before = records.write("case", "race", {"value": 0}, tenant="a")

    def update(value):
        try:
            records.write("case", "race", {"value": value}, previous=before, tenant="a")
            return True
        except ManagedStateConflict:
            return False

    with ThreadPoolExecutor(2) as pool:
        assert sum(pool.map(update, [1, 2])) == 1
    with pytest.raises(RuntimeError):
        with records.locked("owner", "a", tenant="a"):
            records.write("case", "rolled-back", {"created": True}, tenant="a")
            raise RuntimeError("abort")
    assert records.read("case", "rolled-back", tenant="a") is None
    assert records.read("owner", "a", tenant="a") is None


def test_atomic_batch_rolls_back_earlier_writes_on_later_conflict(settings):
    records = LocalRecords(config(settings))
    existing = records.write("case", "existing", {}, tenant="a")
    from backend.app.store.azure_records import record_id

    created = {**existing, "id": record_id("case", "new"), "key": "new"}
    with pytest.raises(ManagedStateConflict):
        records.atomic_batch(
            [
                ("create", (created,), {}),
                ("replace", (existing["id"], existing), {"if_match_etag": "stale"}),
            ],
            tenant="a",
        )
    assert records.read("case", "new", tenant="a") is None


def test_originals_immutable_survive_restart_and_reject_traversal(settings):
    cfg = config(settings)
    records = LocalRecords(cfg)
    data = b"%PDF-fictional-original"
    identifier = hashlib.sha256(data).hexdigest()
    with principal_scope(Principal("a", "owner")):
        files = LocalDocuments(cfg, records)
        files.put(identifier, "original", data, "application/pdf")
        assert LocalDocuments(cfg, LocalRecords(cfg)).get(identifier, "original") == data
        with pytest.raises(ManagedStoreError, match="integrity"):
            files.put(identifier, "original", b"different", "application/pdf")
        with pytest.raises(ValueError):
            files.get("../outside", "original")
    with principal_scope(Principal("b", "owner")):
        with pytest.raises(ManagedStoreError):
            files.get(identifier, "original")


def test_chroma_roundtrip_tenant_filter_metadata_and_vectors(settings):
    cfg = config(settings)
    records = LocalRecords(cfg)
    vector, _, _ = knowledge_stores(cfg, connect_graph=False, records=records)
    metadata = {
        "doc_id": "doc",
        "embedding_provider": "stub",
        "embedding_model": "sha256-unit-vector-v1",
        "content_hash": "digest",
        "page": 2,
        "extra": {"preserved": True},
        "optional": None,
    }
    chunk = {
        "id": "same-id",
        "text": "Fictional VPN policy",
        "metadata": metadata,
        "embedding": [1.0] + [0.0] * 383,
    }
    with principal_scope(Principal("a", "owner")):
        vector.upsert([chunk])
    with principal_scope(Principal("b", "owner")):
        assert vector.search_keywords("VPN") == []
        assert vector.search(chunk["embedding"]) == []
        vector.upsert([{**chunk, "text": "Other tenant VPN"}])
    restarted, _, _ = knowledge_stores(cfg, connect_graph=False)
    with principal_scope(Principal("a", "owner")):
        found = restarted.search(chunk["embedding"])
        assert len(found) == 1 and found[0]["text"] == chunk["text"]
        assert found[0]["metadata"] == metadata
        assert restarted.contains_hash("digest")


def test_staged_documents_survive_blocked_ai_without_cloud_storage(settings, monkeypatch):
    cfg = config(settings, azure_usage_guard_enabled=True, azure_emergency_disable=True)

    def forbidden(*args, **kwargs):
        pytest.fail("Local persistence must not construct a cloud persistence client")

    monkeypatch.setattr("backend.app.store.azure_knowledge.AzureBlobDocuments", forbidden)
    monkeypatch.setattr("backend.app.store.azure_knowledge.AzureSearchStore", forbidden)
    repo = LocalDocumentRepository(cfg, extractor=SimpleNamespace(), embeddings=SimpleNamespace())
    with principal_scope(Principal("a", "owner")):
        result = repo.upload(b"Fictional readable VPN instructions", "Fictional", "text/plain")
        assert result["storage"] == "local"
        with pytest.raises(AzureUsageBlocked):
            repo.process(result["id"])
        restarted = LocalDocumentRepository(
            cfg, extractor=SimpleNamespace(), embeddings=SimpleNamespace()
        )
        assert restarted.get(result["id"])["status"] == "waiting"
        assert restarted.search("VPN", 3)[0]["text"] == "Fictional readable VPN instructions"
        assert (
            restarted.blobs.get(result["id"], "original") == b"Fictional readable VPN instructions"
        )


def test_snapshot_has_no_ephemeral_wal_files_and_stable_hash(settings, tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "backup.db"
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE evidence(value)")
        db.execute("INSERT INTO evidence VALUES('preserved')")
        db.commit()
        report = snapshot_sqlite(source, target)
        gc.collect()
        assert not list(tmp_path.glob("backup.db-*"))
        with sqlite3.connect(target) as backup:
            assert backup.execute("SELECT value FROM evidence").fetchone()[0] == "preserved"
        assert hashlib.sha256(target.read_bytes()).hexdigest() == report["sha256"]


def test_unlimited_daily_restarts_still_obey_emergency_concurrency_and_budget(settings):
    cfg = config(
        settings,
        azure_usage_guard_enabled=True,
        azure_max_live_sessions_per_day=0,
        azure_estimated_monthly_app_limit_cad=8,
        azure_max_voice_session_minutes=2,
    )
    guard = AzureUsageGuard(cfg)
    # New stores fail closed until an operator initializes their policy.
    with pytest.raises(AzureUsageBlocked):
        guard.reserve("session_voice")
    records = LocalRecords(cfg)
    records.write(
        "usage_policy",
        "subscription",
        {"tables": {"azure_policy": [{"id": 1, "disabled": 0}]}},
        tenant="system:usage",
    )
    for _ in range(3):
        token = guard.reserve("session_voice")
        with pytest.raises(AzureUsageBlocked, match="one live"):
            guard.reserve("session_voice")
        guard.finish(token, seconds=120)
    assert guard.snapshot()["enabled"]
    assert guard.snapshot()["estimated_used_cad"] == 3
    guard.disable()
    with pytest.raises(AzureUsageBlocked, match="disabled"):
        guard.reserve("session_voice")


@pytest.mark.asyncio
async def test_local_checkpoint_pending_writes_and_parent_survive_restart(settings):
    from backend.app.agent.azure_checkpoints import CosmosCheckpointSaver
    from langgraph.checkpoint.base import empty_checkpoint

    cfg = config(settings)
    first = CosmosCheckpointSaver(LocalRecords(cfg))
    with principal_scope(Principal("tenant-a", "owner")):
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {"fictional": "preserved context"}
        saved = await first.aput(
            {"configurable": {"thread_id": "case-1", "checkpoint_ns": ""}},
            checkpoint,
            {"source": "input", "step": 0},
            {},
        )
        await first.aput_writes(saved, [("answer", {"text": "pending fact"})], "task-1")
        restarted = CosmosCheckpointSaver(LocalRecords(cfg))
        found = await restarted.aget_tuple(saved)
        assert found.checkpoint == checkpoint
        assert found.pending_writes == [("task-1", "answer", {"text": "pending fact"})]
    with principal_scope(Principal("tenant-b", "owner")):
        assert await restarted.aget_tuple(saved) is None


def test_offline_migration_checks_manifest_and_refuses_existing_destinations(settings, tmp_path):
    import json

    from backend.app.services.local_migration import migrate, verify_backup

    root = tmp_path / "backup"
    root.mkdir()
    data = b"preserved export"
    source = root / "records.jsonl"
    source.write_bytes(data)
    manifest = {
        "verified": True,
        "stable_records": True,
        "files": {
            "records.jsonl": {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    assert verify_backup(root)["verified"]
    LocalRecords(config(settings))
    with pytest.raises(ValueError, match="fresh local"):
        migrate(root, config(settings))
    source.write_bytes(b"modified export")
    with pytest.raises(ValueError, match="integrity"):
        verify_backup(root)


@pytest.mark.asyncio
async def test_local_pdf_pipeline_keeps_original_and_legacy_ingest_response(settings, monkeypatch):
    import base64

    from backend.app.mcp import server
    from backend.app.models.dto import IngestPdfResponse

    cfg = config(settings, embed_provider="azure_openai", embed_model_name="text-embedding-3-small")
    monkeypatch.setattr(
        "backend.app.services.azure_documents.AzureUsageGuard",
        lambda s: SimpleNamespace(require_enabled=lambda: None),
    )
    calls = []

    def extract(data, media):
        calls.append("extract")
        return {"pages": [{"page": 1, "text": "Fictional PDF VPN policy"}], "tables": []}

    def embed(texts):
        calls.append("embed")
        return [[1.0] + [0.0] * 383 for _ in texts]

    repo = LocalDocumentRepository(
        cfg,
        extractor=SimpleNamespace(extract=extract),
        embeddings=SimpleNamespace(embed_texts=embed),
    )
    monkeypatch.setattr(server, "get_settings", lambda: cfg)
    monkeypatch.setattr(server, "documents", lambda: repo)
    data = b"%PDF-fictional-original-binary"
    result = await server.ingest_document(base64.b64encode(data).decode(), "Fictional PDF")
    assert result.status == "ok"
    response = IngestPdfResponse.model_validate(result.result)
    assert response.pages == 1 and response.chunks == 1 and calls == ["extract", "embed"]
    assert repo.blobs.get(response.content_hash, "original") == data
    duplicate = await server.ingest_document(base64.b64encode(data).decode(), "Fictional PDF")
    assert duplicate.result["duplicate"] and calls == ["extract", "embed"]


def test_local_document_processing_retains_free_tier_cost_policy():
    from backend.app.core.config import Settings
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="Paid document analysis"):
        Settings(_env_file=None, local_persistence_enabled=True, azure_document_free_tier=False)


def test_migration_artifact_paths_cannot_escape_local_storage():
    from pathlib import Path

    from backend.app.services.local_migration import artifact_path

    safe = "a" * 64 + "/" + "b" * 64 + "/original"
    assert artifact_path(safe) == Path(safe)
    assert artifact_path("releases/private.zip") is None
    for path in ["../../original", "/outside/original", "a/../../original", "a/b/original"]:
        with pytest.raises(ValueError, match="Unsafe"):
            artifact_path(path)
