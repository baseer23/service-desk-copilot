"""Explicitly opted-in integration tests for Neo4j Aura ingestion."""

from __future__ import annotations

import os

import pytest

try:  # pragma: no cover - optional dependency in CI
    from neo4j import GraphDatabase
except ImportError:  # pragma: no cover
    GraphDatabase = None  # type: ignore


@pytest.mark.slow
@pytest.mark.live
def test_ingestion_with_aura(make_client, tmp_path):  # pragma: no cover - slow path
    if os.getenv("RUN_AURA_TESTS") != "1":
        pytest.skip("Set RUN_AURA_TESTS=1 with isolated Aura credentials")
    if GraphDatabase is None:
        pytest.skip("neo4j driver not installed")

    uri = os.getenv("NEO4J_URI")
    user = os.getenv("NEO4J_USER")
    password = os.getenv("NEO4J_PASSWORD")
    if not uri or not user or not password:
        pytest.skip("Neo4j credentials not configured")

    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        with driver.session() as session:
            session.run("RETURN 1").consume()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Neo4j not reachable: {exc}")

    client = make_client(
        {
            "GENERATION_PROVIDER": "stub",
            "EMBED_PROVIDER": "stub",
            "CHROMA_DIR": tmp_path / "chroma",
            "NEO4J_URI": uri,
            "NEO4J_USER": user,
            "NEO4J_PASSWORD": password,
        }
    )

    try:
        ingest_resp = client.post(
            "/ingest/paste",
            json={"title": "Neo4j Doc", "text": "Graph upserts should succeed."},
        )
        ingest_resp.raise_for_status()
    finally:
        client.close()

    with driver.session() as session:
        result = session.run(
            "MATCH (d:Document {title: $title}) RETURN count(d) AS c", title="Neo4j Doc"
        )
        count = result.single()["c"]
    driver.close()
    assert count >= 1
