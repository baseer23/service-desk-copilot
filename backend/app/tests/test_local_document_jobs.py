import sqlite3
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from backend.app.core.principal import Principal, principal_scope
from backend.app.services.azure_document_formats import word_segments
from backend.app.services.document_repository import DocumentRepository
from backend.app.services.local_backup import RecordMigration, digest, snapshot_sqlite
from backend.app.store.local_records import LocalRecords
from backend.app.store.records import ManagedStoreError
from backend.app.voice.auth import Actor, VoiceError
from backend.app.voice.record_repository import RecordVoiceConversationRepository
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
    records = LocalRecords(settings)
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




def test_ambiguous_document_dispatch_is_never_automatically_repeated(settings, monkeypatch):
    records = LocalRecords(settings)
    blobs = Blobs()
    attempts = []

    def failed(*args):
        attempts.append(1)
        raise RuntimeError("Explicit fictional network failure after dispatch")

    repo = DocumentRepository(
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


def test_record_voice_case_event_and_intake_are_atomic_and_owner_scoped(settings):
    records = LocalRecords(settings)
    repo = RecordVoiceConversationRepository(records)
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


def test_local_embedding_failure_can_be_retried_without_reextracting_or_losing_original(settings):
    records, files, calls = LocalRecords(settings), Blobs(), []
    def embed(texts):
        calls.append(list(texts))
        if len(calls) == 1:
            raise RuntimeError("Local model temporarily unavailable")
        return [[1.0] + [0.0] * 383 for text in texts]
    repository = DocumentRepository(
        settings, records, files, SimpleNamespace(upsert=lambda chunks: None),
        SimpleNamespace(), SimpleNamespace(embed_texts=embed),
    )
    document = repository.upload(b"Preserved local instructions", "Local procedure", "text/plain")
    with pytest.raises(RuntimeError, match="Local model"):
        repository.process(document["id"])
    assert repository.get(document["id"])["phase"] == "embed"
    assert repository.process(document["id"])["status"] == "ready"
    assert len(calls) == 2
    assert files.get(document["id"], "original") == b"Preserved local instructions"
