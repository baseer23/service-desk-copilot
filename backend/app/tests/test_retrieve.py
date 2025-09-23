from types import SimpleNamespace

from backend.app.adapters.embeddings import StubEmbeddingProvider
from backend.app.rag.retrieve import Retriever


class VectorStub:
    def __init__(self, results):
        self.results = results
        self.queries = []

    def search(self, embedding, top_k):
        self.queries.append((embedding, top_k))
        return self.results[:top_k]


class GraphStub:
    def __init__(self, degrees=None, chunks=None):
        self._degrees = degrees or {}
        self._chunks = chunks or []

    def get_entity_degrees(self, names):
        return {name: self._degrees.get(name, 0) for name in names}

    def fetch_chunks_for_entities(self, names, limit):
        return self._chunks[:limit]


sample_vector_results = [
    {"id": "chunk-1", "metadata": {"doc_id": "doc-1"}, "score": 0.1, "text": "chunk"},
    {"id": "chunk-2", "metadata": {"doc_id": "doc-2"}, "score": 0.2, "text": "chunk"},
]


def test_vector_search_returns_results():
    retriever = Retriever(
        settings=SimpleNamespace(top_k=2),
        vector_store=VectorStub(sample_vector_results),
        graph_repo=GraphStub(),
        embedding_provider=StubEmbeddingProvider(dim=4),
    )
    results = retriever.vector_search("Widget Alpha", top_k=1)
    assert len(results) == 1
    assert results[0]["id"] == "chunk-1"


def test_graph_search_uses_graph_repo():
    retriever = Retriever(
        settings=SimpleNamespace(top_k=2),
        vector_store=VectorStub(sample_vector_results),
        graph_repo=GraphStub(degrees={"widget alpha": 2}, chunks=sample_vector_results),
        embedding_provider=StubEmbeddingProvider(dim=4),
    )
    results = retriever.graph_search("Widget Alpha details", top_k=2)
    assert len(results) == 2


def test_hybrid_filters_vector_results():
    retriever = Retriever(
        settings=SimpleNamespace(top_k=2),
        vector_store=VectorStub(sample_vector_results),
        graph_repo=GraphStub(degrees={"widget alpha": 2}, chunks=sample_vector_results[:1]),
        embedding_provider=StubEmbeddingProvider(dim=4),
    )
    results = retriever.hybrid_search("Widget Alpha details", top_k=2)
    assert len(results) == 1
    assert results[0]["id"] == "chunk-1"
