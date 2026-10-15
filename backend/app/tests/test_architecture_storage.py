"""Permanent local persistence has no cloud database adapters or provisioning path."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.app.core.principal import Principal, principal_scope
from backend.app.services.local_backup import snapshot_sqlite
from backend.app.services.local_documents import LocalDocumentRepository
from backend.app.tests.test_ingest_pdf_guard import MINIMAL_PDF


ROOT = Path(__file__).resolve().parents[3]


def test_retired_cloud_database_modules_and_dependencies_are_absent():
    for name in [
        "backend/app/store/azure_records.py", "backend/app/store/azure_knowledge.py",
        "backend/app/services/azure_documents.py", "backend/app/agent/azure_checkpoints.py",
        "backend/app/repositories/azure_memory.py", "scripts/provision_azure.py",
        "scripts/initialize_azure_data.py", "scripts/deploy_containerapps.py",
    ]:
        assert not (ROOT / name).exists(), name
    requirements = (ROOT / "requirements.txt").read_text().casefold()
    for dependency in ["azure-cosmos", "azure-storage-blob", "azure-search-documents", "azure-ai-voicelive"]:
        assert dependency not in requirements


def test_native_pdf_and_local_vectors_keep_original_local(settings):
    config = settings.model_copy(update={
        "local_persistence_enabled": True, "local_documents_dir": settings.case_db_path.parent / "originals",
    })
    embeddings = SimpleNamespace(provider="stub", model_name="sha256-unit-vector-v1", embed_texts=lambda values: [[1.0] + [0.0] * 383 for value in values])
    with principal_scope(Principal("tenant", "owner")):
        repository = LocalDocumentRepository(config, embeddings=embeddings)
        result = repository.upload(MINIMAL_PDF, "Native PDF procedure", "application/pdf")
        completed = repository.process(result["id"])
        assert completed["status"] == "ready" and completed["pages"] == 1
        assert repository.blobs.get(result["id"], "original") == MINIMAL_PDF
        restarted = LocalDocumentRepository(config, embeddings=embeddings)
        assert restarted.search("Hello PDF", 3)[0]["metadata"]["page"] == 1
        assert restarted.blobs.get(result["id"], "original") == MINIMAL_PDF


def test_snapshot_never_overwrites_an_existing_backup(tmp_path):
    import sqlite3
    source, target = tmp_path / "source.sqlite3", tmp_path / "preserved.sqlite3"
    with sqlite3.connect(source) as database:
        database.execute("CREATE TABLE evidence(value)")
    target.write_bytes(b"preserved previous backup")
    with pytest.raises(ValueError, match="fresh"):
        snapshot_sqlite(source, target)
    assert target.read_bytes() == b"preserved previous backup"


def test_local_snapshot_includes_immutable_documents_and_detects_tampering(settings, tmp_path):
    import sqlite3
    from backend.app.services.local_backup import snapshot_sources, verify_local_snapshot
    config = settings.model_copy(update={"local_documents_dir": tmp_path / "originals"})
    with sqlite3.connect(config.case_db_path) as database:
        database.execute("CREATE TABLE preserved(value)")
    config.local_documents_dir.mkdir()
    (config.local_documents_dir / "original.pdf").write_bytes(MINIMAL_PDF)
    destination = tmp_path / "private-snapshot"
    snapshot_sources(config, destination)
    verified = verify_local_snapshot(destination)
    assert verified["verified"] and verified["files_verified"] == 2
    assert (destination / "documents/original.pdf").read_bytes() == MINIMAL_PDF
    (destination / "documents/original.pdf").write_bytes(b"modified backup")
    with pytest.raises(ValueError, match="integrity"):
        verify_local_snapshot(destination)
    assert (config.local_documents_dir / "original.pdf").read_bytes() == MINIMAL_PDF


def test_native_pdf_parser_timeout_and_invalid_input_are_actionable(monkeypatch):
    import subprocess
    from backend.app.services.local_pdf import extract_pdf_pages
    assert extract_pdf_pages(MINIMAL_PDF)[0]["text"].strip() == "Hello PDF"
    with pytest.raises(ValueError, match="parsed"):
        extract_pdf_pages(b"not a PDF")
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("private parser", 35)
    monkeypatch.setattr("backend.app.services.local_pdf.subprocess.run", timeout)
    with pytest.raises(ValueError, match="timed out"):
        extract_pdf_pages(MINIMAL_PDF)


def test_local_snapshot_rejects_symlink_sources_without_reading_targets(settings, tmp_path):
    import sqlite3
    from backend.app.services.local_backup import snapshot_sources
    config = settings.model_copy(update={"local_documents_dir": tmp_path / "originals"})
    with sqlite3.connect(config.case_db_path) as database:
        database.execute("CREATE TABLE preserved(value)")
    config.local_documents_dir.mkdir()
    external = tmp_path / "private-external"
    external.write_bytes(b"must never be copied")
    (config.local_documents_dir / "link.pdf").symlink_to(external)
    destination = tmp_path / "refused-snapshot"
    with pytest.raises(ValueError, match="symbolic links"):
        snapshot_sources(config, destination)
    assert (destination / "documents/link.pdf").is_symlink()
    assert external.read_bytes() == b"must never be copied"
