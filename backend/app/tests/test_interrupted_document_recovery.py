"""Offline recovery of document jobs a restarted application left mid-operation."""

import asyncio
from io import BytesIO
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from backend.app.services.document_processing import AzureDocumentExtractor
from backend.app.services.document_repository import DocumentRepository
from backend.app.services.document_worker import recover_interrupted_work
from backend.app.store.local_knowledge import LocalDocuments
from backend.app.store.local_records import LocalRecords

pytestmark = pytest.mark.unit
UPLOAD = ("application/pdf", "structured_graph", "azure_di")


def _repository(settings, tmp_path):
    configured = settings.model_copy(update={"local_documents_dir": tmp_path / "documents"})
    records = LocalRecords(configured)
    blobs = LocalDocuments(configured, records)
    repository = DocumentRepository(
        configured, records, blobs, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(),
    )
    return repository, records, blobs


def _set_state(records, identifier, **changes):
    row = records.read("document", identifier)
    records.write("document", identifier, {**row["payload"], **changes}, previous=row)


def _pdf(pages):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_recovery_reports_interrupted_extraction_and_requeues_local_stages(settings, tmp_path):
    repository, records, _blobs = _repository(settings, tmp_path)
    ids = {
        name: repository.upload(f"%PDF-1.4 fictional {name}".encode(), f"{name}.pdf", *UPLOAD)["id"]
        for name in ("extracting", "embedding", "structuring", "ready", "failed")
    }
    _set_state(records, ids["extracting"], status="extracting", phase="extract_inflight",
               completed_batches=6)
    _set_state(records, ids["embedding"], status="embedding", phase="embedding_inflight")
    _set_state(records, ids["structuring"], status="structuring", phase="structure")
    _set_state(records, ids["ready"], status="ready", phase="complete")
    _set_state(records, ids["failed"], status="error", phase="extract_inflight",
               processing_error_code="AZURE_DOCUMENT_CAPACITY")

    recovered = {item["id"]: item["status"] for item in repository.recover_interrupted()}

    assert recovered == {
        ids["extracting"]: "error", ids["embedding"]: "queued", ids["structuring"]: "queued",
    }
    interrupted = repository.get(ids["extracting"])
    # The batch that was in flight may have been billed, so it is never re-sent
    # automatically: the job stays in its in-flight phase with an honest error.
    assert interrupted["phase"] == "extract_inflight"
    assert interrupted["processing_error_code"] == "AZURE_DOCUMENT_INTERRUPTED"
    assert interrupted["failed_stage"] == "azure_extraction"
    assert "6 batches saved" in interrupted["processing_error_reason"]
    assert repository.get(ids["embedding"])["phase"] == "embed"
    assert repository.get(ids["structuring"])["phase"] == "structure"
    assert repository.get(ids["ready"])["status"] == "ready"
    assert repository.get(ids["failed"])["processing_error_code"] == "AZURE_DOCUMENT_CAPACITY"
    assert repository.recover_interrupted() == []


def test_reupload_after_interruption_resumes_from_saved_batches(settings, tmp_path):
    repository, records, blobs = _repository(settings, tmp_path)
    data = b"%PDF-1.4 fictional interrupted ferry schedule"
    identifier = repository.upload(data, "Interrupted.pdf", *UPLOAD)["id"]
    batch = b'{"pages": [{"page": 1, "text": "First batch"}]}'
    blobs.put(identifier, "extraction-batch-0.json", batch, "application/json")
    _set_state(records, identifier, status="extracting", phase="extract_inflight",
               completed_batches=1)
    repository.recover_interrupted()

    again = repository.upload(data, "Interrupted.pdf", *UPLOAD)

    assert again["duplicate"] is True and again["status"] == "queued"
    state = repository.get(identifier)
    assert state["phase"] == "extract" and state["completed_batches"] == 1
    assert "processing_error_code" not in state
    assert blobs.location(identifier, "extraction-batch-0.json").read_bytes() == batch
    assert blobs.location(identifier, "original").read_bytes() == data


def test_resumed_extraction_sends_only_the_unsaved_pages(settings, tmp_path):
    configured = settings.model_copy(update={"azure_document_free_tier": True})
    extractor = AzureDocumentExtractor.__new__(AzureDocumentExtractor)
    extractor.settings = configured
    sent = []

    def extract(segment, media_type, enhanced=False):
        sent.append(media_type)
        return {"pages": [{"page": 1, "text": "Unsaved page"}], "paragraphs": [],
                "tables": [], "figures": []}

    extractor.extract = extract
    path = tmp_path / "three-pages.pdf"
    path.write_bytes(_pdf(3))
    saved = {0: {"pages": [{"page": 1, "text": "Saved"}, {"page": 2, "text": "Saved"}],
                 "paragraphs": [], "tables": [], "figures": []}}

    result = extractor.extract_path(
        path, "application/pdf", True, load_batch=saved.get,
        save_batch=lambda index, batch: saved.__setitem__(index, batch),
    )

    assert sent == ["application/pdf"]  # Only the second batch (page 3) is sent.
    assert sorted(saved) == [0, 1]
    assert [page["page"] for page in result["pages"]] == [1, 2, 3]


def test_worker_recovery_runs_per_tenant_and_never_blocks_startup(settings):
    configured = settings.model_copy(update={"auth_allowed_tenants": ["tenant-a", "tenant-b"]})
    calls = []

    class Harness:
        async def invoke(self, name, arguments):
            calls.append(name)
            raise RuntimeError("fictional MCP outage")

    asyncio.run(recover_interrupted_work(configured, Harness()))

    assert calls == ["recover_interrupted_documents"] * 2
