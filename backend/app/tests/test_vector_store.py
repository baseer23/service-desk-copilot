from backend.app.store.vector_chroma import VectorChromaStore
from backend.app.adapters.embeddings import StubEmbeddingProvider


def test_chroma_upsert_and_search(tmp_path):
    store_path = tmp_path / "chroma"
    store = VectorChromaStore(path=str(store_path))

    provider = StubEmbeddingProvider(dim=4)
    embeddings = provider.embed_texts(["chunk one", "chunk two"])

    documents = [
        {
            "id": "chunk-1",
            "text": "chunk one",
            "metadata": {"doc_id": "doc", "ord": 0},
            "embedding": embeddings[0],
        },
        {
            "id": "chunk-2",
            "text": "chunk two",
            "metadata": {"doc_id": "doc", "ord": 1},
            "embedding": embeddings[1],
        },
    ]
    store.upsert(documents)

    query_embedding = embeddings[0]
    results = store.search(query_embedding, top_k=1)
    assert len(results) == 1
    assert results[0]["id"] == "chunk-1"
    assert "text" in results[0]
