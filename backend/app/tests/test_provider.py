"""Baseline contract tests for provider-independent behaviour."""

from __future__ import annotations

from backend.app.core.config import DEFAULT_STUB_ANSWER


def test_health_returns_status_and_provider(monkeypatch, make_client, tmp_path):
    client = make_client(
        {
            "MODEL_PROVIDER": "stub",
            "EMBED_PROVIDER": "stub",
            "CHROMA_DIR": tmp_path / "chroma",
        }
    )

    import backend.app.main as main

    monkeypatch.setattr(main, "_probe_ollama", lambda *_: False, raising=False)
    monkeypatch.setattr(main, "_probe_llamacpp", lambda *_: False, raising=False)
    monkeypatch.setattr(main, "_probe_neo4j", lambda *_: False, raising=False)
    monkeypatch.setattr(main, "_probe_hosted", lambda *_: False, raising=False)
    monkeypatch.setattr(main, "_vector_store_state", lambda *_: (str(tmp_path / "chroma"), True), raising=False)

    try:
        response = client.get("/health")
    finally:
        client.close()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["provider"] == "stub"
    assert body["provider_type"] == "stub"
    assert body["model_name"] == "stub"
    assert body["provider_vendor"] is None
    assert body["local_model_available"] is False
    assert body["operator_message"].startswith("Stub provider active")
    assert body["hosted_reachable"] is False
    assert isinstance(body["preferred_local_models"], list)
    assert "phi3:mini" in body["preferred_local_models"]
    assert body["hosted_model_name"] == "llama-3.1-8b-instant"
    assert body["active_provider"] == "stub"
    assert body["active_model"] == "stub"
    assert body["graph_backend"] == "inmemory"


def test_ask_returns_stub_answer(make_client, tmp_path):
    client = make_client(
        {
            "MODEL_PROVIDER": "stub",
            "EMBED_PROVIDER": "stub",
            "CHROMA_DIR": tmp_path / "chroma",
        }
    )

    try:
        response = client.post("/ask", json={"question": "hello"})
    finally:
        client.close()

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == DEFAULT_STUB_ANSWER
    assert body["provider"] == "stub"
    assert body["question"] == "hello"
    assert isinstance(body.get("citations"), list)


def test_ask_rejects_payload_over_one_megabyte(make_client, tmp_path):
    client = make_client(
        {
            "MODEL_PROVIDER": "stub",
            "EMBED_PROVIDER": "stub",
            "CHROMA_DIR": tmp_path / "chroma",
        }
    )

    try:
        big_question = "a" * (1024 * 1024 + 1)
        response = client.post("/ask", json={"question": big_question})
    finally:
        client.close()

    assert response.status_code == 413
    assert response.json()["detail"] == "Payload too large"
