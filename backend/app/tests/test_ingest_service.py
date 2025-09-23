from types import SimpleNamespace

from backend.app.adapters.embeddings import StubEmbeddingProvider
from backend.app.services.ingest_service import IngestService


class FakeVectorStore:
    def __init__(self):
        self.records = []

    def upsert(self, chunks):
        self.records.extend(chunks)

    def search(self, query_embedding, top_k):  # pragma: no cover - not used in unit test
        return []


class FakeGraphRepo:
    def __init__(self):
        self.documents = []
        self.chunks = []
        self.entities = []

    def ensure_constraints(self):  # pragma: no cover
        pass

    def upsert_document(self, doc_id, title=None, source="paste"):
        self.documents.append((doc_id, title, source))

    def upsert_chunk(self, doc_id, chunk_id, ord, text, token_count):
        self.chunks.append((doc_id, chunk_id, ord, token_count))

    def link_doc_chunk(self, doc_id, chunk_id):
        pass

    def upsert_entity(self, name, type="TERM"):
        entity_id = name.lower()
        self.entities.append(entity_id)
        return entity_id

    def link_chunk_entity(self, chunk_id, entity_id, rel="ABOUT"):
        pass


def test_ingest_text_stub_pipeline():
    settings = SimpleNamespace(
        chunk_tokens=32,
        chunk_overlap=8,
        embed_provider="stub",
        ollama_embed_model="nomic-embed-text",
        embed_model_name="all-MiniLM-L6-v2",
    )
    vector_store = FakeVectorStore()
    graph_repo = FakeGraphRepo()
    embeddings = StubEmbeddingProvider(dim=8)
    service = IngestService(settings=settings, vector_store=vector_store, graph_repo=graph_repo, embedding_provider=embeddings)

    response = service.ingest_text("Manual", "Widget Alpha connects to Widget Beta.")

    assert response.chunks >= 1
    assert response.vector_count == len(vector_store.records)
    assert len(graph_repo.entities) >= 1
