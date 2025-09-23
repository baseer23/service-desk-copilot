import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "stub")
    from backend.app.main import app

    return TestClient(app)


def test_health_returns_status_and_provider(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "provider": "stub"}


def test_ask_returns_stub_answer(client):
    payload = {"question": "hello"}
    response = client.post("/ask", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "hi, this was a test you pass"
    assert body["provider"] == "stub"
    assert body["question"] == "hello"
    assert "citations" in body
    assert isinstance(body["citations"], list)


def test_ask_rejects_payload_over_one_megabyte(client):
    big_question = "a" * (1024 * 1024 + 1)
    response = client.post("/ask", json={"question": big_question})
    assert response.status_code == 413
    assert response.json()["detail"] == "Payload too large"
