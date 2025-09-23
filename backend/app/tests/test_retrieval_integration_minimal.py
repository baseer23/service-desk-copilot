"""Integration-ish tests for retriever behaviour with Chroma + graph."""

from __future__ import annotations

from types import SimpleNamespace

from backend.app.rag.retrieve import Retriever
from backend.app.store.graph_repo import InMemoryGraphRepository
from backend.app.store.vector_chroma import VectorChromaStore


class DummyEmbedder:
    def __init__(self, mapping):
        self.mapping = mapping

    def embed_texts(self, texts):
        return [self.mapping[text] for text in texts]


def _chunk(text, doc_id, chunk_id, ord_, embedding):
    return {
        "id": chunk_id,
        "text": text,
        "metadata": {"doc_id": doc_id, "ord": ord_},
        "embedding": embedding,
    }


def test_vector_and_hybrid_retrieval(tmp_path):
    store = VectorChromaStore(path=str(tmp_path / "chroma"))
    graph = InMemoryGraphRepository()

    graph.upsert_document("doc1", "Alpha Doc")
    graph.upsert_chunk("doc1", "doc1-0", 0, "Alpha systems overview", 10)
    graph.upsert_chunk("doc1", "doc1-1", 1, "Alpha follow up", 10)
    graph.link_doc_chunk("doc1", "doc1-0")
    graph.link_doc_chunk("doc1", "doc1-1")
    alpha_id = graph.upsert_entity("Alpha")
    graph.link_chunk_entity("doc1-0", alpha_id)
    graph.link_chunk_entity("doc1-1", alpha_id)

    graph.upsert_document("doc2", "Beta Doc")
    graph.upsert_chunk("doc2", "doc2-0", 0, "Beta operations log", 10)
    graph.link_doc_chunk("doc2", "doc2-0")
    beta_id = graph.upsert_entity("Beta")
    graph.link_chunk_entity("doc2-0", beta_id)

    store.upsert(
        [
            _chunk("Alpha systems overview", "doc1", "doc1-0", 0, [1.0, 0.0, 0.0]),
            _chunk("Beta operations log", "doc2", "doc2-0", 0, [0.0, 1.0, 0.0]),
            _chunk("Alpha follow up", "doc1", "doc1-1", 1, [0.8, 0.1, 0.0]),
        ]
    )

    embedder = DummyEmbedder({
        "Tell me about Alpha": [1.0, 0.0, 0.0],
    })
    retriever = Retriever(
        settings=SimpleNamespace(top_k=3),
        vector_store=store,
        graph_repo=graph,
        embedding_provider=embedder,
    )

    vector_ids = [item["id"] for item in retriever.vector_search("Tell me about Alpha", top_k=3)]
    assert vector_ids[:2] == ["doc1-0", "doc1-1"]

    hybrid_ids = [item["id"] for item in retriever.hybrid_search("Tell me about Alpha", top_k=3)]
    assert hybrid_ids == ["doc1-0", "doc1-1"]
