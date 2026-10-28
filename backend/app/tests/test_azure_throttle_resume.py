"""Offline Azure Document Intelligence throttling: 429-only retry and duplicate resume."""

from types import SimpleNamespace

import pytest
from azure.core.exceptions import ServiceResponseTimeoutError
from azure.core.pipeline import Pipeline
from azure.core.pipeline.transport import HttpRequest, HttpResponse, HttpTransport
from backend.app.services.document_processing import (
    AzureDocumentExtractor,
    ThrottleOnlyRetryPolicy,
)
from backend.app.services.document_repository import DocumentRepository
from backend.app.store.local_knowledge import LocalDocuments
from backend.app.store.local_records import LocalRecords

pytestmark = pytest.mark.unit


class FakeResponse(HttpResponse):
    def __init__(self, request, status, headers=None):
        super().__init__(request, None)
        self.status_code = status
        self.headers = headers or {}
        self.reason = "fake"

    def body(self):
        return b"{}"


class FakeTransport(HttpTransport):
    """Returns scripted outcomes; never opens a socket."""

    def __init__(self, outcomes):
        self.outcomes, self.calls, self.slept = list(outcomes), 0, []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def open(self):
        pass

    def close(self):
        pass

    def sleep(self, duration):
        self.slept.append(duration)

    def send(self, request, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(request, *outcome)


def _policy():
    return ThrottleOnlyRetryPolicy(
        retry_total=4, retry_status=4, retry_connect=0, retry_read=0,
        retry_backoff_factor=2, retry_backoff_max=60,
    )


def _send(transport):
    request = HttpRequest("POST", "https://fictional.cognitiveservices.azure.com/analyze")
    return Pipeline(transport=transport, policies=[_policy()]).run(request)


def test_throttle_policy_retries_only_429_and_honours_retry_after():
    throttled = FakeTransport([(429, {"Retry-After": "1"}), (200,)])
    assert _send(throttled).http_response.status_code == 200
    assert throttled.calls == 2 and throttled.slept == [1.0]

    server_error = FakeTransport([(500,), (200,)])
    assert _send(server_error).http_response.status_code == 500
    assert server_error.calls == 1

    timeout = FakeTransport([ServiceResponseTimeoutError("fictional timeout"), (200,)])
    with pytest.raises(ServiceResponseTimeoutError):
        _send(timeout)
    assert timeout.calls == 1

    too_long = FakeTransport([(429, {"Retry-After": "120"}), (200,)])
    assert _send(too_long).http_response.status_code == 429
    assert too_long.calls == 1


def test_extractor_client_uses_throttle_only_policy(settings):
    configured = settings.model_copy(update={
        "azure_document_endpoint": "https://fictional.cognitiveservices.azure.com",
    })
    extractor = AzureDocumentExtractor(configured, records=None)
    assert isinstance(extractor.client._config.retry_policy, ThrottleOnlyRetryPolicy)


def test_reupload_resumes_throttled_document_and_keeps_saved_batches(settings, tmp_path):
    configured = settings.model_copy(update={"local_documents_dir": tmp_path / "documents"})
    records = LocalRecords(configured)
    blobs = LocalDocuments(configured, records)
    repository = DocumentRepository(
        configured, records, blobs, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(),
    )
    data = b"%PDF-1.4 fictional throttled harbour permit"
    upload = (data, "Throttled.pdf", "application/pdf", "structured_graph", "azure_di")
    identifier = repository.upload(*upload)["id"]

    # Persist the state a throttled extraction leaves behind: one finished batch.
    batch = b'{"pages": [{"page": 1, "text": "First batch"}]}'
    blobs.put(identifier, "extraction-batch-0.json", batch, "application/json")
    row = records.read("document", identifier)
    records.write("document", identifier, {
        **row["payload"], "status": "error", "phase": "extract_inflight", "completed_batches": 1,
        "processing_error_code": "AZURE_DOCUMENT_CAPACITY",
        "processing_error_reason": "Azure Document Intelligence is temporarily at capacity.",
        "failed_stage": "extract",
    }, previous=row)

    again = repository.upload(*upload)
    assert again["duplicate"] is True and again["status"] == "queued"
    state = repository.get(identifier)
    assert state["phase"] == "extract" and state["completed_batches"] == 1
    assert "processing_error_code" not in state
    assert blobs.location(identifier, "extraction-batch-0.json").read_bytes() == batch
    assert blobs.location(identifier, "original").read_bytes() == data
