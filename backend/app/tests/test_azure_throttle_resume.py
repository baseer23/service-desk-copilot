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


# --- Timeouts, resumed polling and parsing failures ------------------------------------


class _Poller:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)

    def continuation_token(self):
        return "operation-token"

    @property
    def details(self):
        return {"operation_id": "op-1"}

    def result(self, timeout=None):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _Client:
    """Scripted Document Intelligence client; records every analyze call."""

    def __init__(self, submits):
        self.submits, self.calls = list(submits), []

    def begin_analyze_document(self, model_id, body=None, *, continuation_token=None, **kwargs):
        self.calls.append({"body": body is not None, "continuation_token": continuation_token})
        outcome = self.submits.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _result(lines=("Fictional harbour permit",)):
    page = SimpleNamespace(page_number=1, spans=[], lines=[SimpleNamespace(content=v) for v in lines],
                           selection_marks=[], width=8.5, height=11, unit="inch")
    return SimpleNamespace(pages=[page], content="\n".join(lines), paragraphs=[], tables=[],
                           figures=[], sections=[])


def _extractor(settings, client):
    return AzureDocumentExtractor(settings, records=None, client=client)


def test_a_timed_out_status_poll_resumes_the_same_operation_without_resubmitting(settings):
    client = _Client([
        _Poller([ServiceResponseTimeoutError("fictional poll timeout")]),
        _Poller([_result()]),
    ])
    extracted = _extractor(settings, client).extract(b"%PDF fictional", "image/png")
    assert extracted["pages"][0]["text"] == "Fictional harbour permit"
    # One billable submit; the second call only resumes the operation by token.
    assert client.calls == [{"body": True, "continuation_token": None},
                            {"body": False, "continuation_token": "operation-token"}]


def test_a_submit_timeout_is_reported_as_a_timeout_and_never_resent(settings):
    from backend.app.services.document_processing import DocumentProcessingFailure

    client = _Client([ServiceResponseTimeoutError("fictional submit timeout")])
    with pytest.raises(DocumentProcessingFailure) as failure:
        _extractor(settings, client).extract(b"%PDF fictional", "image/png")
    assert failure.value.code == "AZURE_DOCUMENT_TIMEOUT"
    assert failure.value.source_exception_type == "ServiceResponseTimeoutError"
    assert len(client.calls) == 1


def test_our_parsing_errors_are_not_reported_as_azure_failures(settings):
    from backend.app.services.document_processing import DocumentProcessingFailure

    broken = _result()
    broken.pages[0].lines = [SimpleNamespace()]  # a line without content
    client = _Client([_Poller([broken])])
    with pytest.raises(DocumentProcessingFailure) as failure:
        _extractor(settings, client).extract(b"%PDF fictional", "image/png")
    assert failure.value.code == "DOCUMENT_PARSING_FAILED"
    assert failure.value.source_exception_type == "AttributeError"
    assert "document_processing.py" in failure.value.safe_traceback


@pytest.mark.parametrize("code, resumes", [
    ("DOCUMENT_PARSING_FAILED", True), ("AZURE_DOCUMENT_REJECTED", True),
    ("AZURE_DOCUMENT_UNREACHABLE", True), ("AZURE_DOCUMENT_TIMEOUT", False),
    ("AZURE_DOCUMENT_RESPONSE_LOST", False), ("AZURE_DOCUMENT_PROCESSING_FAILED", False),
])
def test_re_adding_resumes_only_after_a_non_ambiguous_failure(settings, tmp_path, code, resumes):
    configured = settings.model_copy(update={"local_documents_dir": tmp_path / "documents"})
    records = LocalRecords(configured)
    repository = DocumentRepository(
        configured, records, LocalDocuments(configured, records), SimpleNamespace(),
        SimpleNamespace(), SimpleNamespace(),
    )
    upload = (b"%PDF-1.4 fictional " + code.encode(), "Retry.pdf", "application/pdf",
              "structured_graph", "azure_di")
    identifier = repository.upload(*upload)["id"]
    row = records.read("document", identifier)
    records.write("document", identifier, {
        **row["payload"], "status": "error", "phase": "extract_inflight",
        "processing_error_code": code, "failed_stage": "azure_extraction",
    }, previous=row)
    again = repository.upload(*upload)
    assert (again["status"] == "queued") is resumes
