import sqlite3
import json
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
        settings,
        records,
        files,
        SimpleNamespace(upsert=lambda chunks: None),
        SimpleNamespace(),
        SimpleNamespace(embed_texts=embed),
    )
    document = repository.upload(b"Preserved local instructions", "Local procedure", "text/plain")
    with pytest.raises(RuntimeError, match="Local model"):
        repository.process(document["id"])
    assert repository.get(document["id"])["phase"] == "embed"
    assert repository.process(document["id"])["status"] == "ready"
    assert len(calls) == 2
    assert files.get(document["id"], "original") == b"Preserved local instructions"


def test_layout_tables_and_extraction_provenance_are_indexed_by_page(settings):
    records, files, indexed = LocalRecords(settings), Blobs(), []

    class Search:
        def upsert(self, chunks):
            indexed[:] = chunks

    extraction = {
        "pages": [{"page": 3, "text": "Support matrix"}],
        "tables": [
            {
                "page": 3,
                "cells": [
                    {"row": 0, "column": 0, "text": "Product"},
                    {"row": 0, "column": 1, "text": "Supported"},
                    {"row": 1, "column": 0, "text": "DeskMate"},
                    {"row": 1, "column": 1, "text": "Yes"},
                ],
            }
        ],
        "method": "document_intelligence",
        "metadata": {"classification": "table_document", "table_count": 1},
    }
    repository = DocumentRepository(
        settings,
        records,
        files,
        Search(),
        SimpleNamespace(extract=lambda *_: extraction),
        SimpleNamespace(
            provider="sentence_transformers",
            model_name="all-MiniLM-L6-v2",
            embed_texts=lambda texts: [[1.0] + [0.0] * 383 for _ in texts],
        ),
    )
    document = repository.upload(b"fictional-image", "Support screenshot", "image/png")
    result = repository.process(document["id"])

    assert result["status"] == "ready"
    assert "Product | Supported DeskMate | Yes" in indexed[0]["text"]
    assert indexed[0]["metadata"]["page"] == 3
    assert indexed[0]["metadata"]["extraction_method"] == "document_intelligence"
    assert indexed[0]["metadata"]["document_classification"] == "table_document"
    assert indexed[0]["metadata"]["table_count"] == 1


def test_structured_mode_preserves_objects_and_stable_local_ids(settings):
    records, files, indexed = LocalRecords(settings), Blobs(), []

    class Search:
        def upsert(self, chunks):
            indexed[:] = chunks

    extraction = {
        "pages": [{"page": 5, "text": "VPN recovery", "markdown": "# VPN recovery", "spans": []}],
        "paragraphs": [
            {
                "id": "p1",
                "page": 5,
                "text": "VPN recovery",
                "role": "sectionHeading",
                "position": 0,
            },
            {
                "id": "p2",
                "page": 5,
                "text": "Check the gateway and certificate.",
                "role": None,
                "position": 1,
            },
        ],
        "sections": [
            {"id": "s1", "page": 5, "title": "VPN recovery", "level": 2, "parent_id": None}
        ],
        "tables": [
            {
                "id": "t1",
                "page": 5,
                "row_count": 2,
                "column_count": 2,
                "cells": [
                    {"row": 0, "column": 0, "row_span": 1, "column_span": 1, "text": "Code"},
                    {"row": 0, "column": 1, "row_span": 1, "column_span": 1, "text": "Action"},
                    {"row": 1, "column": 0, "row_span": 1, "column_span": 1, "text": "VPN42"},
                    {
                        "row": 1,
                        "column": 1,
                        "row_span": 1,
                        "column_span": 1,
                        "text": "Renew certificate",
                    },
                ],
            }
        ],
        "figures": [
            {
                "id": "f1",
                "page": 5,
                "caption": "Gateway flow",
                "related_text": ["Client to gateway"],
                "image_status": "available",
                "image_base64": "aW1hZ2U=",
            }
        ],
        "method": "document_intelligence",
        "metadata": {"structured": True, "classification": "table_document"},
    }
    repository = DocumentRepository(
        settings,
        records,
        files,
        Search(),
        SimpleNamespace(extract=lambda *_: extraction),
        SimpleNamespace(
            provider="sentence_transformers",
            model_name="all-MiniLM-L6-v2",
            embed_texts=lambda texts: [[1.0] + [0.0] * 383 for _ in texts],
        ),
    )
    document = repository.upload(
        b"fictional-structured-pdf", "Structured guide", "application/pdf", "structured_graph"
    )
    result = repository.process(document["id"])
    assert result["status"] == "linking" and result["ingestion_mode"] == "structured_graph"
    assert {row["metadata"]["object_type"] for row in indexed} == {
        "Chunk",
        "Section",
        "Table",
        "Figure",
    }
    assert all(row["metadata"]["page"] == 5 for row in indexed)
    assert any(
        "Code | Action" in row["text"] and "VPN42 | Renew certificate" in row["text"]
        for row in indexed
    )
    assert any(row["metadata"]["figure_id"] == "f1" for row in indexed)
    stored = json.loads(files.get(document["id"], "extraction.json"))
    assert stored["tables"][0]["cells"][0]["row_span"] == 1
    assert "image_base64" not in stored["figures"][0]
    assert files.get(document["id"], stored["figures"][0]["image_artifact"]) == b"image"
