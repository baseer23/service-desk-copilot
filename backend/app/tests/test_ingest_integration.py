import os

import pytest

from backend.app.adapters.embeddings import StubEmbeddingProvider
from backend.app.core.config import Settings
from backend.app.services.ingest_service import IngestService
from backend.app.store.graph_repo import GraphRepository
from backend.app.store.vector_chroma import VectorChromaStore

try:  # pragma: no cover - optional dependency
    from neo4j import GraphDatabase
except ImportError:  # pragma: no cover
    GraphDatabase = None  # type: ignore


@pytest.mark.slow
def test_ingest_pipeline_with_real_neo4j(tmp_path):
    if GraphDatabase is None:
        pytest.skip("neo4j driver not installed")

    uri = os.getenv("NEO4J_URI")
    user = os.getenv("NEO4J_USER")
    password = os.getenv("NEO4J_PASSWORD")
    if not uri or not user or not password:
        pytest.skip("Neo4j connection settings not provided")

    driver = GraphDatabase.driver(uri, auth=(user, password))
    repo = GraphRepository(driver)
    repo.ensure_constraints()

    vector_store = VectorChromaStore(path=str(tmp_path / "chroma"))
    service = IngestService(
        settings=Settings(),
        vector_store=vector_store,
        graph_repo=repo,
        embedding_provider=StubEmbeddingProvider(dim=16),
    )

    response = service.ingest_text("Integration Manual", "Alpha connects to Beta")
    assert response.chunks >= 1
    assert response.vector_count >= 1
