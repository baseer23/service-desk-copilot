import json
import sqlite3
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from backend.app.core.azure_usage import AzureUsageBlocked
from backend.app.core.principal import Principal, principal_scope
from backend.app.services.azure_document_formats import word_segments
from backend.app.services.azure_documents import AzureDocumentRepository
from backend.app.services.azure_migration import RecordMigration, digest, snapshot_sqlite
from backend.app.store.azure_records import CosmosRecords, ManagedStoreError
from backend.app.testing.cosmos import FakeCosmosContainer
from backend.app.voice.auth import Actor, VoiceError
from backend.app.voice.azure_repository import CosmosVoiceConversationRepository
from backend.app.voice.models import VoiceStatus


class Blobs:
    def __init__(self):
        self.items = {}

    def put(self, identifier, artifact, data, media):
        key = (identifier, artifact)
        if key in self.items and self.items[key] != data:
            raise ValueError("Immutable artifact mismatch")
        self.items[key] = data

    def get(self, identifier, artifact):
        return self.items[identifier, artifact]


def test_snapshot_includes_committed_wal_and_never_changes_source(tmp_path):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE original(value TEXT)")
        db.execute("INSERT INTO original VALUES('fictional preserved evidence')")
        db.commit()
        report = snapshot_sqlite(source, tmp_path / "private" / "snapshot.db")
        assert len(report["sha256"]) == 64
        assert (
            db.execute("SELECT value FROM original").fetchone()[0] == "fictional preserved evidence"
        )
        with sqlite3.connect(tmp_path / "private" / "snapshot.db") as backup:
            assert (
                backup.execute("SELECT value FROM original").fetchone()[0]
                == "fictional preserved evidence"
            )


def test_migration_resumes_idempotently_rejects_conflicts_and_retains_changed_records(settings):
    records = CosmosRecords(settings, FakeCosmosContainer())
    migration = RecordMigration(records, "fictional-migration")
    assert digest({"seconds": 0.0}) == digest({"seconds": 0})
    with principal_scope(Principal("tenant-a", "user-a")):
        assert migration.copy("case", "one", {"evidence": "preserved"})
        assert not migration.copy("case", "one", {"evidence": "preserved"})
        with pytest.raises(ManagedStoreError, match="conflicts"):
            migration.copy("case", "one", {"evidence": "overwrite attempt"})
        migration.copy("case", "two", {"evidence": "original"})
        records.write("case", "two", {"evidence": "later work"})
        assert migration.rollback_records(migration.manifest) == {
            "removed": 1,
            "retained_changed_records": 1,
        }
        assert records.read("case", "two")["payload"]["evidence"] == "later work"


def test_document_versions_restart_dedup_lexical_publication_and_blocked_embeddings(settings):
    records = CosmosRecords(settings, FakeCosmosContainer())
    blobs, indexed = Blobs(), []
    search = SimpleNamespace(upsert=lambda chunks: indexed.extend(json.loads(json.dumps(chunks))))
    embeddings = SimpleNamespace(
        embed_texts=lambda chunks: pytest.fail("Emergency disabled: embeddings must not run")
    )
    settings = settings.model_copy(
        update={"azure_usage_guard_enabled": True, "azure_emergency_disable": True}
    )
    with principal_scope(Principal("tenant-a", "user-a")):
        repo = AzureDocumentRepository(
            settings, records, blobs, search, SimpleNamespace(), embeddings
        )
        first = repo.upload(b"Fictional VPN version one", "VPN policy", "text/plain")
        second = repo.upload(b"Fictional VPN version two", "VPN policy", "text/plain")
        assert first["version"] == 1 and second["version"] == 2
        assert repo.upload(b"Fictional VPN version one", "VPN policy", "text/plain")["duplicate"]
        with pytest.raises(AzureUsageBlocked):
            repo.process(first["id"])
        assert indexed and all(chunk["embedding"] is None for chunk in indexed)
        assert indexed[0]["metadata"]["source"] == f"/documents/{first['id']}/download"
        restarted = AzureDocumentRepository(
            settings, records, blobs, search, SimpleNamespace(), embeddings
        )
        assert restarted.get(first["id"])["status"] == "waiting"
        assert restarted.get(first["id"])["phase"] == "embed"


def test_ambiguous_document_dispatch_is_never_automatically_repeated(settings, monkeypatch):
    records = CosmosRecords(settings, FakeCosmosContainer())
    blobs = Blobs()
    attempts = []

    def failed(*args):
        attempts.append(1)
        raise RuntimeError("Explicit fictional network failure after dispatch")

    monkeypatch.setattr(
        "backend.app.services.azure_documents.AzureUsageGuard",
        lambda config: SimpleNamespace(require_enabled=lambda: None),
    )
    repo = AzureDocumentRepository(
        settings,
        records,
        blobs,
        SimpleNamespace(upsert=lambda chunks: None),
        SimpleNamespace(extract=failed),
        SimpleNamespace(),
    )
    with principal_scope(Principal("tenant-a", "user-a")):
        document = repo.upload(b"%PDF-fictional", "Fictional document", "application/pdf")
        with pytest.raises(RuntimeError):
            repo.process(document["id"])
        with pytest.raises(ManagedStoreError, match="operator review"):
            repo.process(document["id"])
        assert attempts == [1]
        assert repo.get(document["id"])["phase"] == "extract_inflight"


def test_word_segmentation_preserves_all_paragraphs_and_tables():
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    text = "Fictional VPN " * 160
    xml = f'<w:document xmlns:w="{namespace}"><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Preserved table evidence</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p><w:r><w:t>{text}</w:t></w:r></w:p><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>'
    source = BytesIO()
    with ZipFile(source, "w") as archive:
        archive.writestr("word/document.xml", xml)
    segments = word_segments(source.getvalue())
    assert len(segments) == 2
    combined = "".join(
        ZipFile(BytesIO(data)).read("word/document.xml").decode() for data, _ in segments
    )
    assert combined.count("Preserved table evidence") == 1
    assert combined.count("Fictional VPN") == 480


def test_cloud_voice_case_event_and_intake_are_atomic_and_owner_scoped(settings, monkeypatch):
    records = CosmosRecords(settings, FakeCosmosContainer())
    repo = CosmosVoiceConversationRepository(records)
    actor = Actor("user-a", "tenant-a")
    with principal_scope(Principal("tenant-a", "user-a")):
        state = repo.create(actor)

        def confirm(current):
            current.confirmed_profile = {
                "name": "Fictional Person",
                "email": "fictional@example.invalid",
                "callback_number": "+14165550123",
            }
            current.issue_summary = "Fictional MFA failure"

        repo.update(state.session_id, actor, confirm)
        case_id = repo.create_case(state.session_id, actor, None)
        assert repo.get(state.session_id, actor).case_id == case_id
        assert records.read("case", case_id)["payload"]["voice_intake"]["confirmed"]
        assert len(records.query("event", fields={"case_id": case_id})) == 1
        repo.create_case(state.session_id, actor, None)
        assert len(records.query("event", fields={"case_id": case_id})) == 1
        repo.update(
            state.session_id, actor, lambda current: setattr(current, "status", VoiceStatus.STOPPED)
        )
        with pytest.raises(VoiceError):
            repo.claim(state.session_id, actor, "late")
        with pytest.raises(VoiceError):
            repo.update(
                state.session_id,
                actor,
                lambda current: setattr(current, "status", VoiceStatus.COMPLETED),
            )
