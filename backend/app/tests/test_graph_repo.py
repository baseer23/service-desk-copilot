from unittest.mock import MagicMock

import pytest

from backend.app.store.graph_repo import GraphRepository


@pytest.fixture()
def mock_driver():
    return MagicMock()


def test_ensure_constraints_executes_statements(mock_driver):
    repo = GraphRepository(mock_driver)
    repo.ensure_constraints()
    session = mock_driver.session.return_value.__enter__.return_value
    assert session.run.call_count >= 3
    first_query = session.run.call_args_list[0].args[0]
    assert "CREATE CONSTRAINT" in first_query


def test_upsert_document_invokes_write(mock_driver):
    repo = GraphRepository(mock_driver)
    repo.upsert_document("doc-1", title="Manual")
    assert mock_driver.execute_write.called


def test_upsert_chunk_links_document(mock_driver):
    repo = GraphRepository(mock_driver)
    repo.upsert_chunk("doc-1", "chunk-1", ord=0, text="Hello", token_count=10)
    repo.link_doc_chunk("doc-1", "chunk-1")
    assert mock_driver.execute_write.call_count == 2


def test_upsert_entity_and_link(mock_driver):
    repo = GraphRepository(mock_driver)
    entity_id = repo.upsert_entity("Widget")
    repo.link_chunk_entity("chunk-1", entity_id)
    assert mock_driver.execute_write.call_count == 2


def test_aura_backend_end_to_end(monkeypatch, make_client, tmp_path):
    import sys
    import types

    store = {
        "documents": {},
        "chunks": {},
        "chunk_docs": {},
        "entities": {},
    }

    class FakeResult:
        def __init__(self, records):
            self._records = records

        def single(self):
            return self._records[0] if self._records else None

        def __iter__(self):
            return iter(self._records)

    class FakeTx:
        def run(self, statement, **params):
            if "MERGE (d:Document" in statement:
                store["documents"][params["doc_id"]] = {
                    "title": params.get("title"),
                    "source": params.get("source"),
                }
                return FakeResult([])
            if "MERGE (c:Chunk" in statement:
                store["chunks"][params["chunk_id"]] = {
                    "text": params.get("text", ""),
                    "ord": params.get("ord", 0),
                    "tokens": params.get("token_count", 0),
                }
                return FakeResult([])
            if "MERGE (d)-[:HAS_CHUNK]->(c)" in statement:
                store["chunk_docs"][params["chunk_id"]] = params["doc_id"]
                return FakeResult([])
            if "MERGE (e:Entity" in statement:
                entity_id = params["name"].lower()
                store["entities"].setdefault(
                    entity_id,
                    {"name": params["name"], "type": params.get("type", "TERM"), "chunks": set()},
                )
                return FakeResult([{"id": entity_id}])
            if "MERGE (c)-[:ABOUT]->(e)" in statement:
                entity = store["entities"].setdefault(
                    params["entity_id"],
                    {"name": params["entity_id"], "type": "TERM", "chunks": set()},
                )
                entity["chunks"].add(params["chunk_id"])
                return FakeResult([])
            if "MATCH (e:Entity) WHERE e.id IN $ids OPTIONAL MATCH" in statement:
                results = []
                for entity_id in params["ids"]:
                    degree = len(store["entities"].get(entity_id, {"chunks": set()})["chunks"])
                    results.append({"id": entity_id, "degree": degree})
                return FakeResult(results)
            if "MATCH (e:Entity) WHERE e.id IN $ids MATCH (e)<-[:ABOUT]" in statement:
                records = []
                for entity_id in params["ids"]:
                    entity = store["entities"].get(entity_id)
                    if not entity:
                        continue
                    for chunk_id in entity["chunks"]:
                        chunk = store["chunks"].get(chunk_id, {})
                        doc_id = store["chunk_docs"].get(chunk_id)
                        doc = store["documents"].get(doc_id, {})
                        records.append(
                            {
                                "chunk_id": chunk_id,
                                "text": chunk.get("text", ""),
                                "ord": chunk.get("ord", 0),
                                "doc_id": doc_id,
                                "title": doc.get("title"),
                            }
                        )
                return FakeResult(records)
            if "RETURN 1 AS ok" in statement:
                return FakeResult([{"ok": 1}])
            return FakeResult([])

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def run(self, *_args, **_kwargs):  # pragma: no cover - constraint setup no-op
            return FakeResult([])

    class FakeDriver:
        def __init__(self):
            self.closed = False

        def session(self):
            return FakeSession()

        def execute_write(self, func):
            return func(FakeTx())

        def execute_read(self, func):
            return func(FakeTx())

        def verify_connectivity(self):
            return True

        def close(self):
            self.closed = True

    class FakeGraphDatabase:
        @staticmethod
        def driver(uri, auth=None):
            assert uri.startswith("neo4j+s://")
            assert auth is not None
            return FakeDriver()

    fake_module = types.ModuleType("neo4j")
    fake_module.GraphDatabase = FakeGraphDatabase
    monkeypatch.setitem(sys.modules, "neo4j", fake_module)

    client = make_client(
        {
            "MODEL_PROVIDER": "stub",
            "EMBED_PROVIDER": "stub",
            "CHROMA_DIR": tmp_path / "chroma",
            "NEO4J_URI": "neo4j+s://demo.neo4j.io",
            "NEO4J_USER": "neo4j",
            "NEO4J_PASSWORD": "secret",
            "__use_real_repos": True,
        }
    )

    import backend.app.main as main

    try:
        assert main.GraphDatabase is not None
        assert main.GraphDatabase.__name__ == "FakeGraphDatabase"
        uri_value = main.app.state.settings.neo4j_uri
        assert isinstance(uri_value, str)
        assert uri_value == "neo4j+s://demo.neo4j.io"
        repo_initialized, driver_initialized = main._init_graph_repo(main.app.state.settings)
        assert isinstance(repo_initialized, GraphRepository)
        assert driver_initialized is not None
        assert main.app.state.graph_backend == "aura"
        main.app.state.graph_repo = repo_initialized
        main.app.state.graph_driver = driver_initialized

        health = client.get("/health").json()
        assert health["graph_backend"] == "aura"
        assert health["neo4j_reachable"] is True

        repo = main.app.state.graph_repo

        repo.upsert_document("doc-1", title="Aura Doc")
        repo.upsert_chunk("doc-1", "doc-1-0", ord=0, text="Reset MFA instructions", token_count=42)
        repo.link_doc_chunk("doc-1", "doc-1-0")
        entity_id = repo.upsert_entity("MFA")
        repo.link_chunk_entity("doc-1-0", entity_id)

        degrees = repo.get_entity_degrees(["MFA"])
        assert degrees["MFA"] == 1

        records = repo.fetch_chunks_for_entities(["MFA"], limit=5)
        assert len(records) == 1
        assert records[0]["metadata"]["doc_id"] == "doc-1"
        assert records[0]["metadata"]["title"] == "Aura Doc"
    finally:
        client.close()
