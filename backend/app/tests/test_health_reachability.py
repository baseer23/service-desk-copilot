"""Health endpoint should surface reachability signals."""

from __future__ import annotations

from typing import Any


def test_health_reports_reachability(monkeypatch, make_client, tmp_path):
    client = make_client(
        {
            "MODEL_PROVIDER": "stub",
            "EMBED_PROVIDER": "stub",
            "CHROMA_DIR": tmp_path / "chroma",
        }
    )

    import backend.app.main as main

    monkeypatch.setattr(main, "_probe_ollama", lambda *_: True, raising=False)
    monkeypatch.setattr(main, "_probe_llamacpp", lambda *_: False, raising=False)
    monkeypatch.setattr(main, "_probe_neo4j", lambda *_: False, raising=False)

    vector_path = tmp_path / "chroma"

    def fake_vector_state(*_: Any):
        return str(vector_path), True

    monkeypatch.setattr(main, "_vector_store_state", fake_vector_state, raising=False)

    try:
        response = client.get("/health")
    finally:
        client.close()

    body = response.json()
    assert body["status"] == "ok"
    assert body["provider"] == "stub"
    assert body["ollama_reachable"] is True
    assert body["llamacpp_reachable"] is False
    assert body["neo4j_reachable"] is False
    assert body["vector_store_path"] == str(vector_path)
    assert body["vector_store_path_exists"] is True
