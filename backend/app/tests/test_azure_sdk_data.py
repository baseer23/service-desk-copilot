import json
from io import BytesIO
from types import SimpleNamespace

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AccessToken
from azure.core.pipeline.transport import HttpResponse, HttpTransport
from azure.search.documents import SearchClient
from backend.app.core.principal import Principal, principal_scope
from backend.app.services.azure_documents import AzureDocumentExtractor
from backend.app.store.azure_knowledge import AzureSearchStore
from backend.app.store.azure_records import CosmosRecords
from backend.app.testing.cosmos import FakeCosmosContainer


class Credential:
    def get_token(self, *scopes, **kwargs):
        return AccessToken("explicit-unit-test-token", 4102444800)


class Response(HttpResponse):
    def __init__(self, request, status, value, headers=None):
        super().__init__(request, None)
        self.status_code = status
        self.headers = {"content-type": "application/json", **(headers or {})}
        self.content_type = "application/json"
        self.value = value

    def body(self):
        return json.dumps(self.value).encode()

    def iter_bytes(self, **kwargs):
        yield self.body()

    def json(self):
        return self.value

    def read(self):
        return self.body()


class Transport(HttpTransport):
    def __init__(self, callback):
        self.callback = callback
        self.requests = []

    def open(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def send(self, request, **kwargs):
        self.requests.append(request)
        return self.callback(request)


def test_real_search_sdk_serializes_vectors_and_enforces_tenant_filter(settings):
    def reply(request):
        body = json.loads(request.body)
        if "value" in body:
            return Response(
                request,
                200,
                {
                    "value": [
                        {"key": item["id"], "status": True, "statusCode": 200}
                        for item in body["value"]
                    ]
                },
            )
        assert body["filter"] == "tenant_id eq 'tenant-a'"
        assert body["semanticConfiguration"] == "grounded"
        assert body["vectorQueries"][0]["fields"] == "vector"
        return Response(
            request,
            200,
            {
                "value": [
                    {
                        "chunk_id": "section",
                        "text": "Fictional VPN guidance",
                        "metadata_json": json.dumps({"page": 2}),
                        "@search.score": 1.0,
                    }
                ]
            },
        )

    transport = Transport(reply)
    client = SearchClient(
        "https://fictional.search.windows.net",
        "knowledge",
        Credential(),
        transport=transport,
        retry_total=0,
    )
    records = CosmosRecords(settings, FakeCosmosContainer())
    store = AzureSearchStore(settings, client, records)
    with principal_scope(Principal("tenant-a", "user-a")):
        chunk = {
            "id": "section",
            "text": "Fictional VPN guidance",
            "metadata": {"doc_id": "document", "page": 2},
            "embedding": None,
        }
        store.upsert([chunk])
        assert "vector" not in json.loads(transport.requests[-1].body)["value"][0]
        chunk["embedding"] = [0.1] * settings.azure_embedding_dimensions
        store.upsert([chunk])
        assert (
            len(json.loads(transport.requests[-1].body)["value"][0]["vector"])
            == settings.azure_embedding_dimensions
        )
        result = store.hybrid("VPN authentication", chunk["embedding"])[0]
        assert result["metadata"]["page"] == 2
        assert isinstance(result["score"], int | float)
    client.close()


def test_real_document_sdk_sends_binary_stream_and_polls_layout_tables(settings, monkeypatch):
    monkeypatch.setattr(
        "backend.app.services.azure_documents.AzureUsageGuard",
        lambda settings: SimpleNamespace(require_enabled=lambda: None),
    )
    endpoint = "https://fictional.cognitiveservices.azure.com"

    def reply(request):
        if request.method == "POST":
            assert request.url.startswith(
                endpoint + "/documentintelligence/documentModels/prebuilt-layout:analyze"
            )
            assert "api-version=2024-11-30" in request.url
            assert (
                isinstance(request.body, BytesIO)
                and request.body.read() == b"explicit-fictional-image"
            )
            return Response(
                request,
                202,
                {},
                {
                    "operation-location": endpoint
                    + "/documentintelligence/documentModels/prebuilt-layout/analyzeResults/one"
                    + "?api-version=2024-11-30",
                    "retry-after": "0",
                },
            )
        return Response(
            request,
            200,
            {
                "status": "succeeded",
                "createdDateTime": "2026-10-13T00:00:00Z",
                "lastUpdatedDateTime": "2026-10-13T00:00:01Z",
                "analyzeResult": {
                    "apiVersion": "2024-11-30",
                    "modelId": "prebuilt-layout",
                    "content": "Fictional VPN guidance",
                    "pages": [{"pageNumber": 1, "lines": [{"content": "Fictional VPN guidance"}]}],
                    "tables": [
                        {
                            "rowCount": 1,
                            "columnCount": 1,
                            "cells": [
                                {"rowIndex": 0, "columnIndex": 0, "content": "Check connection"}
                            ],
                            "boundingRegions": [{"pageNumber": 1}],
                        }
                    ],
                },
            },
        )

    transport = Transport(reply)
    client = DocumentIntelligenceClient(
        endpoint,
        Credential(),
        api_version="2024-11-30",
        transport=transport,
        retry_total=0,
        polling_interval=0,
    )
    extractor = AzureDocumentExtractor(
        settings, CosmosRecords(settings, FakeCosmosContainer()), client
    )
    result = extractor.extract(b"explicit-fictional-image", "image/png")
    assert result["pages"][0]["text"] == "Fictional VPN guidance"
    assert result["tables"][0]["cells"][0]["text"] == "Check connection"
    assert result["metadata"]["classification_method"] == "layout_rules"
    assert len(transport.requests) == 2
    client.close()


def test_real_cosmos_sdk_does_not_restore_default_throttle_retries():
    from azure.cosmos.cosmos_client import _build_connection_policy
    from backend.app.store.azure_records import connection_policy

    actual = _build_connection_policy(
        {
            "connection_policy": connection_policy(),
            "retry_total": 0,
            "connection_timeout": 5,
            "read_timeout": 10,
        }
    )
    assert actual.RetryOptions.MaxRetryAttemptCount == 0
    assert actual.RetryOptions.MaxWaitTimeInSeconds == 0
    assert actual.RequestTimeout == 5 and actual.ReadTimeout == 10


def test_real_managed_identity_sdk_uses_bounded_token_transport(settings, monkeypatch):
    import time
    from urllib.parse import parse_qs, urlsplit

    from backend.app.store.azure_records import azure_credential

    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://localhost:42356/msi/token")
    monkeypatch.setenv("IDENTITY_HEADER", "explicit-unit-test-header")
    monkeypatch.setenv("AZURE_CLIENT_ID", "fictional-user-assigned-identity")

    def reply(request):
        query = parse_qs(urlsplit(request.url).query)
        assert query["resource"] == ["https://cosmos.azure.com"]
        assert query["client_id"] == ["fictional-user-assigned-identity"]
        return Response(
            request,
            200,
            {
                "access_token": "explicit-unit-test-token",
                "expires_on": int(time.time()) + 3600,
                "expires_in": 3600,
                "token_type": "Bearer",
                "resource": "https://cosmos.azure.com",
            },
        )

    transport = Transport(reply)
    credential = azure_credential(settings.model_copy(update={"deployment_mode": "production"}))
    managed = next(
        item
        for item in credential.credentials
        if type(item).__name__ == "ManagedIdentityCredential"
    )
    real_transport = managed._credential._client._pipeline._transport
    assert real_transport.connection_config.timeout == 5
    assert real_transport.connection_config.read_timeout == 5
    monkeypatch.setattr(real_transport, "send", transport.send)
    try:
        assert (
            credential.get_token("https://cosmos.azure.com/.default").token
            == "explicit-unit-test-token"  # noqa: S105 - Explicit test-only response.
        )
        assert len(transport.requests) == 1
    finally:
        credential.close()
