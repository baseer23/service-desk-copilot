from types import SimpleNamespace

import pytest
from backend.app.core.azure_managed_usage import CosmosUsageGuard
from backend.app.core.azure_usage import AzureUsageBlocked
from backend.app.core.principal import Principal, principal_scope
from backend.app.rag.retrieve import Retriever
from backend.app.store.azure_knowledge import AzureBlobDocuments
from backend.app.store.azure_records import CosmosRecords, ManagedStoreError
from backend.app.testing.cosmos import FakeCosmosContainer


def test_managed_audio_policy_read_does_not_take_writer_lease_and_sees_shutdown(settings):
    records = CosmosRecords(settings, FakeCosmosContainer())
    config = settings.model_copy(update={"azure_usage_guard_enabled": True})
    guard = CosmosUsageGuard(config, records)
    records.write(
        "usage_policy",
        "subscription",
        {"tables": {"azure_policy": [{"id": 1, "disabled": 0}], "azure_usage": []}},
        tenant="system:usage",
    )
    with records.locked("usage_policy", "subscription", tenant="system:usage"):
        guard.require_enabled()  # Readers remain responsive during a reservation.
    records.write(
        "usage_policy",
        "subscription",
        {"tables": {"azure_policy": [{"id": 1, "disabled": 1}], "azure_usage": []}},
        tenant="system:usage",
    )
    with pytest.raises(AzureUsageBlocked, match="disabled"):
        guard.require_enabled()


def test_blob_operation_cap_blocks_before_sdk_dispatch(settings, monkeypatch):
    import time

    records = CosmosRecords(settings, FakeCosmosContainer())
    calls = []
    container = SimpleNamespace(download_blob=lambda *args, **kwargs: calls.append(1))
    blobs = AzureBlobDocuments(settings, container, records)
    month = time.strftime("%Y-%m", time.gmtime())
    records.write("blob_operations", month, {"reads": 10_000}, tenant="system:storage")
    with principal_scope(Principal("tenant-a", "user-a")):
        with pytest.raises(ManagedStoreError, match="request allowance"):
            blobs.get("fictional-document", "original")
    assert calls == []


def test_managed_keyword_retrieval_uses_one_search_and_no_query_embedding():
    queries = []
    search = SimpleNamespace(
        search_keywords=lambda question, count: queries.append(question)
        or [{"id": "one", "text": "Fictional evidence"}]
    )
    documents = SimpleNamespace(
        search=lambda *args: pytest.fail("The document repository shares the same managed index")
    )
    embeddings = SimpleNamespace(
        embed_texts=lambda *args: pytest.fail("Keyword retrieval must not embed the query")
    )
    retriever = Retriever(
        SimpleNamespace(data_platform="azure"), search, None, embeddings, documents
    )
    assert retriever.keyword_search("VPN", 6)[0]["id"] == "one"
    assert queries == ["VPN"]
