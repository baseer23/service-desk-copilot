"""Tests that exercise the provider selection and fallbacks."""

from __future__ import annotations

from typing import Any, Dict

import pytest
import requests

from backend.app.core.config import DEFAULT_STUB_ANSWER

FALLBACK_PREFIX = "Model provider unavailable; falling back to stub. "


def _call_ask(client, question: str = "hello") -> Dict[str, Any]:
    response = client.post("/ask", json={"question": question})
    assert response.status_code == 200
    return response.json()


def test_stub_provider_path(make_client, tmp_path):
    client = make_client(
        {
            "MODEL_PROVIDER": "stub",
            "EMBED_PROVIDER": "stub",
            "CHROMA_DIR": tmp_path / "chroma",
        }
    )
    try:
        body = _call_ask(client, "quick check")
    finally:
        client.close()

    assert body["answer"] == DEFAULT_STUB_ANSWER
    assert body["provider"] == "stub"
    assert isinstance(body.get("citations"), list)


def test_ollama_provider_success(monkeypatch, make_client, tmp_path):
    payload = {
        "MODEL_PROVIDER": "ollama",
        "MODEL_NAME": "phi3:mini",
        "EMBED_PROVIDER": "stub",
        "CHROMA_DIR": tmp_path / "chroma",
    }
    client = make_client(payload)

    def fake_post(url, json, timeout):  # noqa: A002 - required by signature
        assert url.endswith("/api/generate")

        class _Resp:
            status_code = 200

            def raise_for_status(self) -> None:  # noqa: D401 - standard requests API
                """Pretend everything is fine."""

            def json(self) -> Dict[str, Any]:
                return {"response": "Hello from Ollama"}

        return _Resp()

    monkeypatch.setattr(requests, "post", fake_post)

    try:
        body = _call_ask(client, "ol test")
    finally:
        client.close()

    assert body["provider"] == "ollama"
    assert body["answer"] == "Hello from Ollama"
    assert body["answer"] != DEFAULT_STUB_ANSWER


def test_ollama_provider_failure_falls_back(monkeypatch, make_client, tmp_path):
    payload = {
        "MODEL_PROVIDER": "ollama",
        "MODEL_NAME": "phi3:mini",
        "EMBED_PROVIDER": "stub",
        "CHROMA_DIR": tmp_path / "chroma",
    }
    client = make_client(payload)

    def fake_post(*_, **__):
        raise requests.Timeout("simulated timeout")

    monkeypatch.setattr(requests, "post", fake_post)

    try:
        body = _call_ask(client, "timeout test")
    finally:
        client.close()

    assert body["provider"] == "ollama"
    assert body["answer"].startswith(FALLBACK_PREFIX)
    assert DEFAULT_STUB_ANSWER in body["answer"]


def test_llamacpp_provider_success(monkeypatch, make_client, tmp_path):
    payload = {
        "MODEL_PROVIDER": "llamacpp",
        "MODEL_NAME": "custom-model",
        "EMBED_PROVIDER": "stub",
        "CHROMA_DIR": tmp_path / "chroma",
    }
    client = make_client(payload)

    def fake_post(url, json, timeout):  # noqa: A002 - required by signature
        assert url.endswith("/completion")

        class _Resp:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> Dict[str, Any]:
                return {"content": "Hi from llama.cpp"}

        return _Resp()

    monkeypatch.setattr(requests, "post", fake_post)

    try:
        body = _call_ask(client, "llama test")
    finally:
        client.close()

    assert body["provider"] == "llamacpp"
    assert body["answer"] == "Hi from llama.cpp"
    assert body["answer"] != DEFAULT_STUB_ANSWER


def test_admin_toggle_switches_provider(monkeypatch, make_client, tmp_path):
    payload = {
        "MODEL_PROVIDER": "stub",
        "EMBED_PROVIDER": "stub",
        "CHROMA_DIR": tmp_path / "chroma",
        "GROQ_API_KEY": "unit-test",
        "ADMIN_API_SECRET": "secret",
    }
    client = make_client(payload)

    import backend.app.main as main

    monkeypatch.setattr(main, "_probe_hosted", lambda *_: True, raising=False)
    monkeypatch.setattr(main, "_probe_ollama", lambda *_: False, raising=False)

    def fake_groq_post(url, **_kwargs):
        assert "groq" in url

        class _Resp:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> Dict[str, Any]:
                return {"choices": [{"message": {"content": "Groq says hi"}}]}

        return _Resp()

    monkeypatch.setattr(requests, "post", fake_groq_post)

    try:
        response = client.post(
            "/admin/provider",
            json={"provider": "groq"},
            headers={"x-admin-secret": "secret"},
        )
        assert response.status_code == 200
        assert response.json()["active_provider"] == "groq"

        health = client.get("/health").json()
        assert health["active_provider"] == "groq"
        assert health["provider_type"] in {"hosted", "stub"}

        ask_body = client.post("/ask", json={"question": "check"}).json()
        assert ask_body["provider"] == "hosted-groq"
        assert ask_body["answer"] == "Groq says hi"
    finally:
        client.close()


def test_provider_override_only_affects_single_call(monkeypatch, make_client, tmp_path):
    payload = {
        "MODEL_PROVIDER": "stub",
        "EMBED_PROVIDER": "stub",
        "CHROMA_DIR": tmp_path / "chroma",
        "GROQ_API_KEY": "unit-test",
    }
    client = make_client(payload)

    def fake_groq_post(url, **_kwargs):
        assert "groq" in url

        class _Resp:
            status_code = 200

            def raise_for_status(self) -> None:
                return None

            def json(self) -> Dict[str, Any]:
                return {"choices": [{"message": {"content": "Override groq"}}]}

        return _Resp()

    monkeypatch.setattr(requests, "post", fake_groq_post)

    try:
        override = client.post(
            "/ask",
            json={"question": "override", "provider_override": "groq"},
        ).json()
        assert override["provider"] == "hosted-groq"
        assert override["answer"] == "Override groq"

        fallback = client.post("/ask", json={"question": "default"}).json()
        assert fallback["provider"] == "stub"
        assert fallback["answer"] == DEFAULT_STUB_ANSWER
    finally:
        client.close()


def test_hosted_failure_falls_back_to_stub_with_citations(monkeypatch, make_client, tmp_path):
    payload = {
        "MODEL_PROVIDER": "stub",
        "EMBED_PROVIDER": "stub",
        "CHROMA_DIR": tmp_path / "chroma",
        "GROQ_API_KEY": "unit-test",
    }
    client = make_client(payload)

    def failing_post(*_args, **_kwargs):
        raise requests.Timeout("boom")

    monkeypatch.setattr(requests, "post", failing_post)

    try:
        ingest = client.post(
            "/ingest/paste",
            json={"title": "Doc", "text": "Reset MFA by following steps."},
        )
        assert ingest.status_code == 200

        body = client.post(
            "/ask",
            json={"question": "How to reset?", "provider_override": "groq"},
        ).json()

        assert body["provider"] == "hosted-groq"
        assert body["answer"].startswith(FALLBACK_PREFIX)
        assert DEFAULT_STUB_ANSWER in body["answer"]
        assert body["citations"], "Expected citations to be preserved"
    finally:
        client.close()
