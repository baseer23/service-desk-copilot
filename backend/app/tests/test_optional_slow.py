"""Optional slow integration tests for local services."""

from __future__ import annotations

import os

import pytest
import requests

try:  # pragma: no cover - optional dependency in CI
    from neo4j import GraphDatabase
except ImportError:  # pragma: no cover
    GraphDatabase = None  # type: ignore


@pytest.mark.slow
def test_ask_end_to_end_with_neo4j(make_client, tmp_path):  # pragma: no cover - slow path
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
            "MODEL_PROVIDER": "stub",
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
        ask_resp = client.post("/ask", json={"question": "Graph upserts question"})
        ask_resp.raise_for_status()
    finally:
        client.close()

    with driver.session() as session:
        result = session.run("MATCH (d:Document {title: $title}) RETURN count(d) AS c", title="Neo4j Doc")
        count = result.single()["c"]
    driver.close()
    assert count >= 1


@pytest.mark.slow
def test_provider_ollama_live(make_client, tmp_path):  # pragma: no cover - slow path
    host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    try:
        response = requests.get(f"{host.rstrip('/')}/api/tags", timeout=2)
        response.raise_for_status()
    except Exception as exc:
        pytest.skip(f"Ollama not reachable: {exc}")

    client = make_client(
        {
            "MODEL_PROVIDER": "ollama",
            "MODEL_NAME": os.getenv("MODEL_NAME", "phi3:mini"),
            "CHROMA_DIR": tmp_path / "chroma",
            "EMBED_PROVIDER": "stub",
        }
    )

    try:
        body = client.post("/ask", json={"question": "Say hi"}).json()
    finally:
        client.close()

    assert body["provider"] == "ollama"
    assert body["answer"]
    assert body["answer"] != "hi, this was a test you pass"
