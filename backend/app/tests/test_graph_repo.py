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
