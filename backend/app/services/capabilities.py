"""Local knowledge/vector construction with optional real Neo4j Aura enrichment."""

import hashlib
import logging

from backend.app.core.principal import current_principal
from backend.app.store.graph_repo import (
    GraphRepository, InMemoryGraphRepository, UnavailableGraphRepository,
)
from backend.app.store.vector_chroma import VectorChromaStore


def collection_identity(settings):
    """Embedding spaces are isolated even when two models share a dimension."""
    if settings.embed_provider == "stub":
        return "chunks_stub_v1", "sha256-unit-vector-v1", 384
    if settings.embed_provider != "sentence_transformers":
        raise ValueError("Live vector storage requires local SentenceTransformers embeddings")
    identity = "sentence_transformers:" + settings.embed_model_name + ":384:normalized-v1"
    return "chunks_local_" + hashlib.sha256(identity.encode()).hexdigest()[:16], settings.embed_model_name, 384


def knowledge_stores(settings, *, connect_graph=True, records=None):
    name, model, dimensions = collection_identity(settings)
    vector = VectorChromaStore(
        str(settings.chroma_dir), collection_name=name,
        embedding_provider=settings.embed_provider, embedding_model=model,
        embedding_dimensions=dimensions,
    )
    if settings.local_persistence_enabled:
        from backend.app.store.local_knowledge import LocalKnowledgeStore
        from backend.app.store.local_records import LocalRecords

        vector = LocalKnowledgeStore(vector, records or LocalRecords(settings))
    # In-memory graph fixtures are exclusively test evidence. Live fallback
    # contains no invented documents, entities or relationships.
    graph = InMemoryGraphRepository() if settings.test_mode else UnavailableGraphRepository()
    driver = None
    if connect_graph and settings.neo4j_uri:
        try:
            from neo4j import GraphDatabase

            if not settings.neo4j_password.get_secret_value():
                raise ValueError("Aura credentials are missing")
            driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
                connection_timeout=5, max_transaction_retry_time=0,
                connection_acquisition_timeout=5,
            )
            driver.verify_connectivity()
            graph = GraphRepository(
                driver,
                tenant_provider=(lambda: current_principal(settings).tenant_id)
                if settings.local_persistence_enabled else None,
                optional=True, allow_fixtures=settings.test_mode,
            )
            graph.ensure_constraints()
        except Exception:
            if driver:
                driver.close()
            driver = None
            graph = InMemoryGraphRepository() if settings.test_mode else UnavailableGraphRepository()
            logging.getLogger(__name__).warning("Aura unavailable; using vector retrieval")
    return vector, graph, driver
