"""Shared capability construction used by ingestion and the MCP server."""

import logging
import hashlib
from backend.app.store.graph_repo import GraphRepository, InMemoryGraphRepository
from backend.app.store.vector_chroma import VectorChromaStore


def knowledge_stores(settings, *, connect_graph=True):
    if settings.data_platform == "azure":
        from backend.app.store.azure_knowledge import AzureSearchStore

        return AzureSearchStore(settings), InMemoryGraphRepository(), None
    model = (
        "sha256-unit-vector-v1" if settings.embed_provider == "stub" else settings.embed_model_name
    )
    # Legacy chunks have no embedding provenance. Leave them intact; new verified
    # collections isolate test vectors and prevent mixing unknown embedding spaces.
    identity = (
        settings.azure_foundry_endpoint
        + ":"
        + settings.embed_model_name
        + ":"
        + settings.azure_embedding_deployment
        + ":"
        + str(settings.azure_embedding_dimensions)
    )
    name = (
        "chunks_stub_v1"
        if settings.embed_provider == "stub"
        else "chunks_azure_" + hashlib.sha256(identity.encode()).hexdigest()[:16]
    )
    vector = VectorChromaStore(
        str(settings.chroma_dir),
        collection_name=name,
        embedding_provider=settings.embed_provider,
        embedding_model=model,
        embedding_dimensions=(
            384 if settings.embed_provider == "stub" else settings.azure_embedding_dimensions
        ),
    )
    graph, driver = InMemoryGraphRepository(), None
    if connect_graph and settings.neo4j_uri:
        try:
            from neo4j import GraphDatabase

            driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
                connection_timeout=5,
            )
            driver.verify_connectivity()
            graph = GraphRepository(driver)
            graph.ensure_constraints()
        except Exception:
            if driver:
                driver.close()
            driver = None
            logging.getLogger(__name__).warning("Aura unavailable; using vector retrieval")
    return vector, graph, driver
