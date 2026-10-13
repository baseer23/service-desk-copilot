import hashlib
from unittest.mock import Mock
import pytest
from backend.app.testing.embeddings import StubEmbeddingProvider
from backend.app.services.ingest_service import IngestService
from backend.app.services.capabilities import knowledge_stores
from backend.app.store.graph_repo import InMemoryGraphRepository
from backend.app.store.vector_chroma import VectorChromaStore, StorageError


def test_persistent_duplicate_provenance_and_retrieval(settings):
    store, graph, _ = knowledge_stores(settings)
    embedder = StubEmbeddingProvider()
    service = IngestService(settings, store, graph, embedder)
    text = "VPN authentication profile refresh instructions. Install the required client update and retry."
    first = service.ingest_text(
        "VPN guide",
        text,
        source="https://example.com/vpn",
        source_type="web",
        url="https://example.com/vpn",
    )
    assert first.chunks >= 1 and not first.duplicate
    again, _, _ = knowledge_stores(settings)
    second = IngestService(settings, again, InMemoryGraphRepository(), embedder).ingest_text(
        "Retitled", text
    )
    assert second.duplicate and second.chunks == 0 and again.count() == first.chunks
    row = again.search(embedder.embed_texts([text])[0], 1)[0]
    meta = row["metadata"]
    assert meta["content_hash"] == hashlib.sha256(text.encode()).hexdigest()
    assert meta["title"] == "VPN guide" and meta["source_type"] == "web"
    assert meta["url"] == "https://example.com/vpn"
    assert meta["embedding_provider"] == "stub" and meta["embedding_model"] == embedder.model_name
    assert meta["created_at"]


@pytest.mark.parametrize("operation", ["search", "upsert"])
def test_storage_failure_never_resets_or_deletes(tmp_path, operation):
    store = VectorChromaStore(str(tmp_path / "chroma"))
    record = {
        "id": "retained",
        "text": "Retained data",
        "metadata": {"doc_id": "retained"},
        "embedding": [1.0, 0.0, 0.0],
    }
    store.upsert([record])
    original = store._collection
    fake = Mock(wraps=original)
    store._collection = fake
    fake.query.side_effect = TypeError("Simulated read incompatibility")
    fake.upsert.side_effect = TypeError("Simulated write incompatibility")
    with pytest.raises(StorageError, match="preserved"):
        if operation == "search":
            store.search([1.0, 0.0, 0.0])
        else:
            store.upsert([record])
    assert original.count() == 1
    assert original.get(ids=["retained"])["documents"] == ["Retained data"]


def test_embedding_mismatch_and_failure_are_explicit(settings):
    vector, graph, _ = knowledge_stores(settings)
    with pytest.raises(StorageError, match="provenance"):
        vector.upsert(
            [
                {
                    "id": "bad",
                    "text": "bad",
                    "metadata": {
                        "embedding_provider": "sentence",
                        "embedding_model": "all-MiniLM-L6-v2",
                    },
                    "embedding": [0.0] * 384,
                }
            ]
        )
    failing = Mock()
    failing.embed_texts.side_effect = RuntimeError("model failure")
    with pytest.raises(RuntimeError, match="embedding generation failed"):
        IngestService(settings, vector, graph, failing).ingest_text(
            "fail", "This must not become stub data"
        )
    assert vector.count() == 0
