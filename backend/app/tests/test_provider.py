import os
import socket

from starlette.testclient import TestClient


def is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        try:
            s.connect((host, port))
            return True
        except Exception:
            return False


def test_ask_stub_provider(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "stub")
    from backend.app.main import app  # import after env set

    client = TestClient(app)
    r = client.post("/ask", json={"question": "test"})
    assert r.status_code == 200
    data = r.json()
    assert data["answer"] == "hi, this was a test you pass"
    assert data["provider"] == "stub"


def test_provider_field_if_ollama_available(monkeypatch):
    if not is_port_open("127.0.0.1", 11434):
        return  # skip if ollama not reachable locally
    monkeypatch.setenv("MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("MODEL_TIMEOUT_SEC", "3")
    from backend.app.main import app

    client = TestClient(app)
    r = client.post("/ask", json={"question": "Hello"})
    assert r.status_code == 200
    data = r.json()
    assert data["provider"] == "ollama"

