import json
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest
from backend.app.diagnostics import azure_readiness
from backend.app.diagnostics.readiness import configuration_readiness
from backend.app.voice.models import VoiceGreeting
from fastapi.testclient import TestClient
from pydantic import SecretStr


def azure_settings(settings, **extra):
    return settings.model_copy(
        update={
            "test_mode": False,
            "azure_usage_guard_enabled": True,
            "generation_provider": "azure_openai",
            "embed_provider": "azure_openai",
            "voice_provider": "azure_voicelive",
            "azure_foundry_endpoint": "https://private-resource.openai.azure.com",
            "azure_model_deployment": "private-deployment",
            "azure_embedding_deployment": "private-embedding",
            "azure_voicelive_endpoint": "https://private-voice.services.ai.azure.com",
            "azure_use_entra_id": False,
            "azure_openai_api_key": SecretStr("never-print-reasoning-key"),
            "azure_voicelive_api_key": SecretStr("never-print-voice-key"),
            "voice_enabled": True,
            "voice_session_signing_key": SecretStr("never-print-signing-key-xxxxxxxxxxxxxxxxx"),
            **extra,
        }
    )


def test_configured_is_not_authenticated_or_live_validated(settings):
    s = azure_settings(settings)
    report = configuration_readiness(
        s, reasoning_initialized=True, local_mcp="READY", private_stores="READY"
    )
    assert report.voice_ready and report.text_ready
    assert (
        report.reasoning.authenticated == "NOT_CHECKED" and report.voice.reachable == "NOT_CHECKED"
    )
    assert not report.live_azure_validated and not report.voice.live_validated
    encoded = report.model_dump_json()
    assert not any(
        value in encoded
        for value in [
            "private-resource",
            "private-deployment",
            "never-print",
            "private-embedding",
            "private-voice",
        ]
    )


def test_readiness_controls_missing_categories(settings):
    s = azure_settings(settings, azure_model_deployment="", azure_voicelive_endpoint="")
    report = configuration_readiness(
        s, reasoning_initialized=False, local_mcp="READY", private_stores="READY"
    )
    assert not report.text_ready and not report.voice_ready
    assert report.reasoning.missing == ["AZURE_MODEL_DEPLOYMENT"]
    assert "AZURE_VOICELIVE_ENDPOINT" in report.voice.missing
    demo = settings.model_copy(update={"voice_enabled": True, "voice_provider": "fake"})
    report = configuration_readiness(
        demo, reasoning_initialized=True, local_mcp="READY", private_stores="READY"
    )
    assert report.runtime_mode == "isolated_demo" and report.voice_ready
    assert report.voice.authenticated == "NOT_APPLICABLE" and not report.live_azure_validated


def test_missing_azure_boots_readiness_without_canned_answers_or_cloud(make_client, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Azure network/auth is forbidden")

    monkeypatch.setattr("azure.identity.DefaultAzureCredential.get_token", forbidden)
    client = make_client(
        {
            "TEST_MODE": "false",
            "GENERATION_PROVIDER": "azure_openai",
            "EMBED_PROVIDER": "azure_openai",
            "VOICE_PROVIDER": "azure_voicelive",
            "AZURE_FOUNDRY_ENDPOINT": "",
            "AZURE_MODEL_DEPLOYMENT": "",
            "AZURE_EMBEDDING_DEPLOYMENT": "",
            "AZURE_VOICELIVE_ENDPOINT": "",
        }
    )
    report = client.get("/readiness").json()
    assert (
        report["runtime_mode"] == "azure" and not report["text_ready"] and not report["voice_ready"]
    )
    assert report["local_mcp"] == "READY" and report["private_stores"] == "READY"
    for _ in range(3):
        assert client.get("/health").json()["status"] == "degraded"
    response = client.post("/ask", json={"summary": "VPN is broken"})
    assert response.status_code == 503 and "AZURE_REASONING_NOT_READY" in response.text
    assert "Hello" not in response.text


def test_http_readiness_and_health_never_leak_values(make_client):
    client = make_client(
        {
            "AZURE_FOUNDRY_ENDPOINT": "https://private-resource.openai.azure.com",
            "AZURE_MODEL_DEPLOYMENT": "private-deployment",
            "AZURE_OPENAI_API_KEY": "never-print-azure",
            "AZURE_VOICELIVE_API_KEY": "never-print-voice",
            "VOICE_SESSION_SIGNING_KEY": "never-print-signing-xxxxxxxxxxxxxxxxxxxxxxxxxxx",
        }
    )
    for url in ["/health", "/readiness"]:
        text = client.get(url).text
        assert not any(
            value in text
            for value in [
                "private-resource",
                "private-deployment",
                "never-print-azure",
                "never-print-signing",
                "never-print-voice",
            ]
        )
        body = json.loads(text)
        report = body.get("readiness", body)
        assert report["runtime_mode"] == "isolated_demo" and not report["live_azure_validated"]
        if url == "/health":
            assert not body["azure_openai_configured"] and not body["voicelive_configured"]


def test_cli_is_status_only_and_does_not_validate_live_without_flag(settings, monkeypatch, capsys):
    s = azure_settings(settings)
    monkeypatch.setattr(azure_readiness, "Settings", lambda: s)
    monkeypatch.setattr(azure_readiness, "local_checks", AsyncMock(return_value=("READY", "READY")))
    live = AsyncMock(side_effect=AssertionError("No live call authorized"))
    monkeypatch.setattr(azure_readiness, "validate_live", live)
    assert azure_readiness.main([]) == 0
    live.assert_not_called()
    output = capsys.readouterr().out
    assert "NOT_CHECKED" in output and "Live Azure validated" in output
    assert not any(
        value in output
        for value in [
            "private-resource",
            "private-deployment",
            "never-print",
            "private-embedding",
            "private-voice",
        ]
    )


@pytest.mark.asyncio
async def test_live_probe_refuses_test_mode_before_constructing_cloud_clients(settings):
    with pytest.raises(ValueError, match="LIVE_REQUIRES_AZURE_MODE"):
        await azure_readiness.validate_live(settings, configuration_readiness(settings))


def test_real_stdio_readiness_cli_has_status_only_stdout_and_silent_stderr(settings):
    import os
    import subprocess
    import sys

    config = os.environ.copy()
    config.update(
        {
            "TEST_MODE": "true",
            "GENERATION_PROVIDER": "stub",
            "EMBED_PROVIDER": "stub",
            "VOICE_PROVIDER": "fake",
            "VOICE_ENABLED": "true",
            "NEO4J_URI": "",
            "DEPLOYMENT_MODE": "local",
            "VOICE_AUTH_MODE": "local_demo",
            "AZURE_FOUNDRY_ENDPOINT": "",
            "AZURE_MODEL_DEPLOYMENT": "",
            "AZURE_EMBEDDING_DEPLOYMENT": "",
            "AZURE_VOICELIVE_ENDPOINT": "",
            "CHROMA_DIR": str(settings.chroma_dir),
            "CASE_DB_PATH": str(settings.case_db_path),
            "VOICE_SESSION_SIGNING_KEY": "isolated-readiness-signing-xxxxxxxxxxxxxxxxxxxxxxxxx",
        }
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter/module with isolated paths
        [sys.executable, "-m", "backend.app.diagnostics.azure_readiness"],
        env=config,
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )
    assert result.returncode == 0 and not result.stderr
    assert "Local MCP                        READY" in result.stdout
    assert "Private stores                   READY" in result.stdout
    assert str(settings.case_db_path) not in result.stdout
    assert "isolated-readiness-signing" not in result.stdout


def test_azure_text_fallback_has_no_voice_live_connection_and_generates_greeting(
    settings, monkeypatch
):
    from backend.app.main import create_app

    class Reasoning:
        calls: ClassVar[list[str]] = []

        def name(self):
            return "azure_openai"

        async def generate_structured(self, stage, context, schema):
            self.calls.append(stage)
            assert stage == "voice_greeting"
            return VoiceGreeting(
                reply=(
                    "I am your Azure-powered AI support assistant. "
                    "What IT issue needs investigation?"
                )
            )

    model = Reasoning()
    monkeypatch.setattr("backend.app.main.get_provider", lambda s: model)

    def no_voice(*args, **kwargs):
        raise AssertionError("Voice Live must not be constructed for text fallback")

    monkeypatch.setattr("backend.app.voice.azure_voicelive.AzureVoiceLiveProvider", no_voice)
    s = azure_settings(settings, voice_enabled=False, azure_voicelive_endpoint="")
    with TestClient(
        create_app(s), base_url="http://127.0.0.1", client=("127.0.0.1", 12345)
    ) as client:
        report = client.get("/readiness").json()
        assert report["text_ready"] and not report["voice_ready"]
        response = client.post(
            "/voice/sessions", json={"mode": "text"}, headers={"Origin": "http://127.0.0.1"}
        )
        assert response.status_code == 201, response.text
        sid = response.json()["session_id"]
        with client.websocket_connect(
            "ws://127.0.0.1/voice/sessions/" + sid + "/stream",
            headers={"Origin": "http://127.0.0.1"},
        ) as socket:
            for _ in range(12):
                event = socket.receive_json()
                if event["type"] == "transcript.assistant.final":
                    break
            assert "Azure-powered" in event["payload"]["text"]
            assert model.calls == ["voice_greeting"]
            socket.send_json(
                {
                    "version": 1,
                    "type": "session.stop",
                    "session_id": sid,
                    "sequence": 1,
                    "payload": {},
                }
            )


def test_explicitly_stopped_session_cannot_reconnect(make_client):
    client = make_client({"VOICE_ENABLED": "true", "VOICE_PROVIDER": "fake"})
    headers = {"Origin": "http://testserver"}
    sid = client.post("/voice/sessions", json={"mode": "text"}, headers=headers).json()[
        "session_id"
    ]
    client.post("/voice/sessions/" + sid + "/stop", headers=headers)
    response = client.post(
        "/voice/sessions", json={"resume_session_id": sid, "mode": "text"}, headers=headers
    )
    assert response.status_code == 409 and response.json()["code"] == "VOICE_SESSION_TERMINAL"


def test_fresh_local_demo_start_replaces_stale_cookie_without_accessing_old_session(make_client):
    client = make_client({"VOICE_ENABLED": "true", "VOICE_PROVIDER": "fake"})
    headers = {"Origin": "http://testserver"}
    old = client.post("/voice/sessions", json={"mode": "text"}, headers=headers).json()[
        "session_id"
    ]
    client.cookies.clear()
    client.cookies.set("deskmate_voice_identity", "stale-after-demo-restart", path="/")
    assert client.get("/voice/sessions/" + old).status_code == 401
    resume = client.post(
        "/voice/sessions", json={"resume_session_id": old, "mode": "text"}, headers=headers
    )
    assert resume.status_code == 401
    assert client.cookies.get("deskmate_voice_identity") == "stale-after-demo-restart"
    fresh = client.post("/voice/sessions", json={"mode": "text"}, headers=headers)
    assert fresh.status_code == 201 and fresh.json()["session_id"] != old
    assert client.get("/voice/sessions/" + old).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "reasoning", "embeddings", "voice"])
async def test_live_probe_uses_only_bounded_mock_requests_and_silent_handshake(
    settings, monkeypatch, failure
):
    from backend.app.voice.models import VoiceEvent

    calls = []

    class Reasoning:
        client = object()

        def __init__(self, config):
            pass

        async def generate_structured(self, stage, context, schema):
            calls.append("reasoning")
            assert stage == "readiness" and schema is azure_readiness.Probe
            if failure == "reasoning":
                raise RuntimeError("private diagnostic must not be returned")
            return schema(status="ok")

        def close(self):
            calls.append("reasoning.close")

    class Embeddings:
        def __init__(self, config, client):
            assert client is Reasoning.client

        def embed_texts(self, texts):
            calls.append("embeddings")
            assert len(texts) == 1 and "Fictional" in texts[0]
            if failure == "embeddings":
                raise RuntimeError("private embedding response")
            return [[0.0] * 384]

    class Voice:
        def __init__(self, config):
            pass

        async def create_session(self):
            calls.append("voice.open")

        async def events(self):
            calls.append("voice.ack")
            yield VoiceEvent(id="ack", type="error" if failure == "voice" else "configured")

        async def close_session(self):
            calls.append("voice.close")

    monkeypatch.setattr("backend.app.models.provider_azure_openai.AzureOpenAIProvider", Reasoning)
    monkeypatch.setattr("backend.app.adapters.embeddings.AzureEmbeddingProvider", Embeddings)
    monkeypatch.setattr("backend.app.voice.azure_voicelive.AzureVoiceLiveProvider", Voice)
    config = azure_settings(settings)
    report = await azure_readiness.validate_live(config, configuration_readiness(config))
    assert report.live_azure_validated == (failure is None)
    assert calls.count("reasoning") == 1 and calls[-1] == "reasoning.close"
    assert "audio" not in calls and "respond" not in calls
    if failure is None or failure == "voice":
        assert calls[-2] == "voice.close"
    if failure:
        capability = getattr(report, failure)
        assert capability.reachable == "NO" and not capability.live_validated
        assert "private diagnostic" not in report.model_dump_json()
        assert "private embedding response" not in report.model_dump_json()
