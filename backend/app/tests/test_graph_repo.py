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


def test_structured_graph_writes_reference_metadata_without_raw_content():
    captured = []

    class Driver:
        def execute_write(self, callback):
            return callback(
                SimpleNamespace(run=lambda query, **params: captured.append((query, params)))
            )

    from types import SimpleNamespace

    repo = GraphRepository(Driver(), tenant_provider=lambda: "tenant-a")
    repo.upsert_reference_object(
        "a" * 64,
        {
            "object_id": "C17",
            "object_type": "Chunk",
            "page": 5,
            "position": 2,
            "description": "VPN guidance " * 40,
        },
    )
    serialized = repr(captured)
    assert "full private paragraph" not in serialized
    assert all("text" not in params and "embedding" not in params for _, params in captured)
    assert captured[0][1]["tenant_id"] == "tenant-a"
    assert len(captured[0][1]["description"]) == 160


def test_upsert_entity_and_link(mock_driver):
    repo = GraphRepository(mock_driver)
    entity_id = repo.upsert_entity("Widget")
    repo.link_chunk_entity("chunk-1", entity_id)
    assert mock_driver.execute_write.call_count == 2


def test_semantic_search_is_owner_scoped_bounded_and_parameterized():
    captured = []

    class Driver:
        def execute_read(self, callback):
            class Tx:
                def run(self, query, **parameters):
                    captured.append((query, parameters))
                    return []

            return callback(Tx())

    repo = GraphRepository(Driver(), tenant_provider=lambda: "tenant-a")
    hostile = "name' MATCH (n) DETACH DELETE n"
    assert repo.search_semantic_memory("owner-a", hostile, 999) == []
    query, parameters = captured[0]
    assert hostile not in query
    assert parameters["tenant"] == "tenant-a" and parameters["owner"] == "owner-a"
    assert parameters["limit"] == 20 and len(parameters["terms"]) <= 10


def test_aura_repository_end_to_end(monkeypatch, tmp_path):
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
        def driver(uri, auth=None, **kwargs):
            assert uri.startswith("neo4j+s://")
            assert auth is not None
            return FakeDriver()

    fake_module = types.ModuleType("neo4j")
    fake_module.GraphDatabase = FakeGraphDatabase
    monkeypatch.setitem(sys.modules, "neo4j", fake_module)

    from backend.app.core.config import Settings
    from backend.app.services.capabilities import knowledge_stores

    settings = Settings(
        _env_file=None,
        test_mode=True,
        generation_provider="stub",
        embed_provider="stub",
        chroma_dir=tmp_path / "chroma",
        case_db_path=tmp_path / "cases.db",
        neo4j_uri="neo4j+s://demo.neo4j.io",
        neo4j_user="neo4j",
        neo4j_password="secret",
    )
    _, repo, driver = knowledge_stores(settings)
    try:
        assert isinstance(repo, GraphRepository)
        assert driver is not None and repo.ping()
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
        driver.close()


def test_relationship_discovery_is_bounded_parameterized_and_provenanced():
    statements = []

    class Tx:
        def run(self, query, **args):
            statements.append((query, args))
            if "RETURN DISTINCT" in query:
                return [
                    {
                        "subject": "B99 2nd floor",
                        "predicate": "USES",
                        "object": "WatchGuard VPN",
                        "source_system": "local_fixture",
                        "observed_at": "2026-10-08T10:00:00Z",
                    }
                ]
            return []

    class Driver:
        def execute_write(self, fn):
            return fn(Tx())

        def execute_read(self, fn):
            return fn(Tx())

    from backend.app.repositories.fixtures import FixtureRepository

    repo = GraphRepository(Driver())
    repo.upsert_relationships(FixtureRepository().relationships())
    rows = repo.search_relationships(["Jake Peralta"], limit=5)
    assert rows[0]["predicate"] == "USES" and rows[0]["source_system"] == "local_fixture"
    query, arguments = statements[-1]
    assert "[*1..2]" in query and "LIMIT $limit" in query
    assert arguments == {
        "terms": ["jake peralta"],
        "limit": 5,
        "tenant": None,
        "allow_fixtures": True,
    }
    assert any("WORKS_AT" in q for q, _ in statements)
