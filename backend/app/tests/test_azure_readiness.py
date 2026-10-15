"""Status-only diagnostic and explicitly bounded text-only live probe contract."""

from unittest.mock import AsyncMock

import pytest
from backend.app.diagnostics import azure_readiness
from backend.app.diagnostics.readiness import configuration_readiness
from pydantic import SecretStr


def azure_settings(settings, **extra):
    s = settings.model_copy(
        update={
            "test_mode": False,
            "generation_provider": "azure_openai",
            "embed_provider": "sentence_transformers",
            "azure_foundry_endpoint": "https://private-resource.openai.azure.com",
            "azure_model_deployment": "private-deployment",
            "azure_use_entra_id": False,
            "azure_openai_api_key": SecretStr("never-print-reasoning-key"),
            "azure_speech_endpoint": "https://private-speech.cognitiveservices.azure.com/",
            "speech_enabled": True,
            "voice_session_signing_key": SecretStr("never-print-signing-xxxxxxxxxxxxxxxxxxxxxxxx"),
            **extra,
        }
    )
    return s


def test_configured_does_not_claim_live_speech_or_reasoning(settings):
    s = azure_settings(settings)
    report = configuration_readiness(
        s, reasoning_initialized=True, local_mcp="READY", private_stores="READY"
    )
    assert report.text_ready and report.voice_ready
    assert report.voice.reachable == report.reasoning.authenticated == "NOT_CHECKED"
    assert not report.live_azure_validated and not report.voice.live_validated
    encoded = report.model_dump_json()
    assert not any(
        value in encoded
        for value in ("private-resource", "private-deployment", "private-speech", "never-print")
    )


def test_speech_independent_of_llm_provider_and_missing_reasoning(settings):
    s = azure_settings(settings, azure_model_deployment="")
    report = configuration_readiness(
        s, reasoning_initialized=False, local_mcp="READY", private_stores="READY"
    )
    assert not report.text_ready and report.voice_ready
    assert report.reasoning.missing == ["AZURE_MODEL_DEPLOYMENT"]
    assert not configuration_readiness(s.model_copy(update={"speech_enabled": False})).voice_ready


def test_cli_status_only_never_prints_values_or_sends_live_request(settings, monkeypatch, capsys):
    s = azure_settings(settings)
    monkeypatch.setattr(azure_readiness, "Settings", lambda: s)
    monkeypatch.setattr(azure_readiness, "local_checks", AsyncMock(return_value=("READY", "READY")))
    live = AsyncMock(side_effect=AssertionError("No live request authorized"))
    monkeypatch.setattr(azure_readiness, "validate_live", live)
    assert azure_readiness.main([]) == 0
    live.assert_not_called()
    output = capsys.readouterr().out
    assert "Speech STT enabled" in output and "SEPARATE_EXPLICIT_TEST" in output
    assert "Live Azure text validated" in output and "NOT_CHECKED" in output
    assert not any(
        value in output
        for value in ("private-resource", "private-deployment", "private-speech", "never-print")
    )
    assert "Voice Live" not in output


@pytest.mark.asyncio
async def test_live_probe_refuses_test_mode_without_constructing_cloud_clients(settings):
    with pytest.raises(ValueError, match="LIVE_REQUIRES_AZURE_TEXT_MODE"):
        await azure_readiness.validate_live(settings, configuration_readiness(settings))


@pytest.mark.asyncio
async def test_explicit_live_diagnostic_calls_only_one_text_model_and_never_speech(
    settings, monkeypatch
):
    s = azure_settings(settings)
    model = AsyncMock()
    model.close = lambda: None

    def factory(_s):
        return model

    monkeypatch.setattr("backend.app.models.provider_azure_openai.AzureOpenAIProvider", factory)
    monkeypatch.setattr(
        "backend.app.speech.azure_stt.AzureSpeechRecognizer",
        lambda *_args: pytest.fail("Speech request forbidden"),
    )
    report = await azure_readiness.validate_live(s, configuration_readiness(s))
    assert report.reasoning.live_validated and report.live_azure_validated
    assert not report.voice.live_validated and not report.embeddings.live_validated
    model.generate_structured.assert_awaited_once()
    assert model.generate_structured.call_args.args[0] == "readiness"


def test_optional_missing_speech_does_not_fail_local_configuration_diagnostic(
    settings, monkeypatch
):
    s = azure_settings(settings, speech_enabled=False, azure_speech_endpoint="")
    monkeypatch.setattr(azure_readiness, "Settings", lambda: s)
    monkeypatch.setattr(azure_readiness, "local_checks", AsyncMock(return_value=("READY", "READY")))
    assert azure_readiness.main([]) == 0


def test_cli_account_read_is_read_only_and_output_free(monkeypatch):
    from types import SimpleNamespace

    commands = []
    monkeypatch.setattr(azure_readiness.shutil, "which", lambda _name: "/fake/az")

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(azure_readiness.subprocess, "run", run)
    assert azure_readiness.cli_signed_in(False) == "NOT_CHECKED"
    assert not commands
    assert azure_readiness.cli_signed_in(True) == "YES"
    assert commands == [["/fake/az", "account", "show", "--output", "none", "--only-show-errors"]]
