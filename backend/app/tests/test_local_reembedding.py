"""Actual temporary Chroma/SQLite migration, provenance isolation and graph fallback."""

from types import SimpleNamespace

from backend.app.core.principal import Principal, principal_scope
from backend.app.services.capabilities import knowledge_stores
from backend.app.services.local_reembedding import migrate_local_embeddings
from backend.app.store.graph_repo import GraphRepository, UnavailableGraphRepository
from backend.app.store.local_knowledge import LocalKnowledgeStore
from backend.app.store.local_records import LocalRecords
from backend.app.store.vector_chroma import VectorChromaStore


def config(settings):
    return settings.model_copy(update={"local_persistence_enabled": True, "embed_provider": "sentence_transformers", "embed_model_name": "all-MiniLM-L6-v2", "test_mode": False, "local_documents_dir": settings.case_db_path.parent / "originals"})


def seed(cfg):
    records = LocalRecords(cfg)
    old = VectorChromaStore(str(cfg.chroma_dir), collection_name="chunks_azure_preserved", embedding_provider="azure_openai", embedding_model="text-embedding-3-small")
    store = LocalKnowledgeStore(old, records)
    for tenant, title, chunk_id, text in [
        ("a", "Network procedure", "a-0", "VPN authentication procedure"),
        ("b", "Network procedure", "b-0", "Tenant B VPN guidance"),
        ("a", "Fictional acceptance document", "demo-0", "Fictional VPN test material"),
    ]:
        with principal_scope(Principal(tenant, "owner")):
            store.upsert([{"id": chunk_id, "text": text, "embedding": [0.5] * 384,
                "metadata": {"title": title, "doc_id": chunk_id, "embedding_provider": "azure_openai", "embedding_model": "text-embedding-3-small", "custom": {"preserved": True}}}])
    return records, old


def test_local_reembedding_preserves_sources_and_excludes_explicit_test_material(settings):
    cfg = config(settings)
    records, old = seed(cfg)
    before = old._collection.get(include=["documents", "metadatas", "embeddings"])
    dry = migrate_local_embeddings(cfg)
    assert dry["dry_run"] and dry["source_chunks"] == 2
    assert dry["audit_counts"]["explicit_fixtures_preserved_but_not_indexed"] == 1
    assert dry["quarantined_chunks"][0]["chunk_id"] == "demo-0"
    assert dry["target_collection"] not in [c.name for c in old._client.list_collections()]
    provider = SimpleNamespace(embed_texts=lambda values: [[1.0] + [0.0] * 383 for value in values])
    applied = migrate_local_embeddings(cfg, apply=True, provider=provider)
    assert applied["verified"] and applied["indexed"] == 2 and not applied["graph_available"]
    assert old.count() == 3
    after = old._collection.get(include=["documents", "metadatas", "embeddings"])
    assert after["documents"] == before["documents"] and after["metadatas"] == before["metadatas"]
    assert after["embeddings"] == before["embeddings"]
    restarted, graph, _ = knowledge_stores(cfg)
    assert isinstance(graph, UnavailableGraphRepository)
    with principal_scope(Principal("a", "owner")):
        assert restarted.count() == 1
        hits = restarted.search_keywords("VPN", 10)
        assert [row["id"] for row in hits] == ["a-0"]
        found = restarted.search([1.0] + [0.0] * 383)[0]
        assert found["metadata"]["custom"] == {"preserved": True}
        assert found["metadata"]["previous_embedding_provider"] == "azure_openai"
        assert found["metadata"]["embedding_provider"] == "sentence_transformers"
    with principal_scope(Principal("b", "owner")):
        assert restarted.search_keywords("VPN")[0]["id"] == "b-0"
    assert any(c.name.startswith("quarantine_") for c in old._client.list_collections())
    assert migrate_local_embeddings(cfg, apply=True, provider=provider)["source_digest"] == applied["source_digest"]


def test_aura_connectivity_failure_keeps_real_vector_only_evidence(settings, monkeypatch):
    cfg = config(settings).model_copy(update={"neo4j_uri": "neo4j+s://unreachable.neo4j.io", "neo4j_password": SimpleNamespace(get_secret_value=lambda: "private")})
    import neo4j
    monkeypatch.setattr(neo4j.GraphDatabase, "driver", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("private credential")))
    vector, graph, driver = knowledge_stores(cfg)
    assert driver is None and not graph.ping()
    assert graph.fetch_chunks_for_entities(["VPN"], 5) == []
    assert graph.search_relationships(["VPN"]) == []
    assert vector.ping()


def test_graph_adapter_scopes_ids_and_survives_loss_after_connection():
    calls = []
    class Driver:
        def execute_write(self, fn):
            return fn(SimpleNamespace(run=lambda query, **args: calls.append((query, args))))
        def execute_read(self, fn):
            raise RuntimeError("sensitive connection string")
    repo = GraphRepository(Driver(), tenant_provider=lambda: "tenant-a", optional=True, allow_fixtures=False)
    repo.upsert_document("document", title="Guide")
    assert calls[0][1]["doc_id"] != "document" and calls[0][1]["original_id"] == "document"
    assert calls[0][1]["tenant_id"] == "tenant-a"
    assert repo.fetch_chunks_for_entities(["VPN"], 5) == []
    assert repo.get_entity_degrees(["VPN"]) == {"VPN": 0}
    assert repo.search_relationships(["VPN"]) == []
    assert not repo.available
