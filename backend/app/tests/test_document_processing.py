import json
from io import BytesIO
from types import SimpleNamespace

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AccessToken
from azure.core.pipeline.transport import HttpResponse, HttpTransport
from backend.app.services.document_processing import AzureDocumentExtractor
from backend.app.store.local_records import LocalRecords


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


def test_real_document_sdk_sends_binary_stream_and_polls_layout_tables(settings):
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
        settings, LocalRecords(settings), client
    )
    result = extractor.extract(b"explicit-fictional-image", "image/png")
    assert result["pages"][0]["text"] == "Fictional VPN guidance"
    assert result["tables"][0]["cells"][0]["text"] == "Check connection"
    assert result["metadata"]["classification_method"] == "layout_rules"
    assert len(transport.requests) == 2
    client.close()


def test_real_managed_identity_sdk_uses_bounded_token_transport(settings, monkeypatch):
    import time
    from urllib.parse import parse_qs, urlsplit

    from backend.app.core.azure_identity import azure_credential

    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://localhost:42356/msi/token")
    monkeypatch.setenv("IDENTITY_HEADER", "explicit-unit-test-header")
    monkeypatch.setenv("AZURE_CLIENT_ID", "fictional-user-assigned-identity")

    def reply(request):
        query = parse_qs(urlsplit(request.url).query)
        assert query["resource"] == ["https://cognitiveservices.azure.com"]
        assert query["client_id"] == ["fictional-user-assigned-identity"]
        return Response(
            request,
            200,
            {
                "access_token": "explicit-unit-test-token",
                "expires_on": int(time.time()) + 3600,
                "expires_in": 3600,
                "token_type": "Bearer",
                "resource": "https://cognitiveservices.azure.com",
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
            credential.get_token("https://cognitiveservices.azure.com/.default").token
            == "explicit-unit-test-token"  # noqa: S105 - Explicit test-only response.
        )
        assert len(transport.requests) == 1
    finally:
        credential.close()
