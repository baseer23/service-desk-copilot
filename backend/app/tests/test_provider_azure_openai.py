import json
from types import SimpleNamespace

import pytest
from backend.app.agent.models import InvestigationPlan, RequestIntent
from backend.app.models.provider import LLMError
from backend.app.models.provider_azure_openai import AzureOpenAIProvider


def provider(settings, responses):
    payloads = []

    def create(**kwargs):
        payloads.append(kwargs)
        value = responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=value))])

    config = settings.model_copy(
        update={
            "azure_foundry_endpoint": "https://example.openai.azure.com",
            "azure_model_deployment": "test-deployment",
            "azure_usage_guard_enabled": True,
        }
    )
    return (
        AzureOpenAIProvider(
            config,
            SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
        ),
        payloads,
    )


@pytest.mark.asyncio
async def test_readiness_probe_is_one_minimal_request_without_schema_retries(settings):
    from backend.app.diagnostics.azure_readiness import Probe

    model, payloads = provider(settings, ["{}", '{"status":"ok"}'])
    with pytest.raises(LLMError, match="invalid structured"):
        await model.generate_structured("readiness", {}, Probe)
    assert len(payloads) == 1 and payloads[0]["max_completion_tokens"] == 80


@pytest.mark.asyncio
async def test_azure_structured_validation_and_bounded_correction(settings):
    model, payloads = provider(
        settings,
        [
            "{}",
            json.dumps({"workflow": "knowledge", "goal": "Question", "justification": "Documents"}),
        ],
    )
    result = await model.generate_structured(
        "classify_intent", {"request": "Question"}, RequestIntent
    )
    assert result.workflow == "knowledge" and model.name() == "azure_openai"
    assert len(payloads) == 2
    assert payloads[0]["response_format"]["json_schema"]["strict"]
    assert payloads[0]["timeout"] == settings.model_timeout_sec


@pytest.mark.asyncio
async def test_azure_failure_is_explicit_and_private(settings):
    model, payloads = provider(settings, [RuntimeError("private key and sensitive body")])
    with pytest.raises(LLMError, match="Azure OpenAI request failed") as exc:
        await model.generate_structured("classify_intent", {}, RequestIntent)
    assert "private" not in str(exc.value) and len(payloads) == 1
    model, payloads = provider(settings, ["{}", "{}"])
    with pytest.raises(LLMError, match="invalid structured"):
        await model.generate_structured("classify_intent", {}, RequestIntent)
    assert len(payloads) == 2


@pytest.mark.asyncio
async def test_free_tool_argument_maps_use_validated_json_mode(settings):
    model, payloads = provider(
        settings,
        [
            json.dumps(
                {
                    "goal": "Read",
                    "tool_calls": [
                        {"name": "search_knowledge", "arguments": {"question": "Question"}}
                    ],
                    "stop_condition": "Done",
                }
            )
        ],
    )
    result = await model.generate_structured("plan", {}, InvestigationPlan)
    assert result.tool_calls[0].arguments["question"] == "Question"
    assert payloads[0]["response_format"] == {"type": "json_object"}


def test_provider_factory_and_entra_authentication(settings, monkeypatch):
    from backend.app.models.provider_factory import get_provider

    captured = {}
    token_supplier = object()

    def sdk(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("openai.AzureOpenAI", sdk)
    monkeypatch.setattr("azure.identity.DefaultAzureCredential", lambda **kwargs: object())
    monkeypatch.setattr(
        "azure.identity.get_bearer_token_provider", lambda credential, scope: token_supplier
    )
    config = settings.model_copy(
        update={
            "generation_provider": "azure_openai",
            "azure_foundry_endpoint": "https://example.openai.azure.com",
            "azure_model_deployment": "deployment",
        }
    )
    assert get_provider(config).name() == "azure_openai"
    assert captured["azure_ad_token_provider"] is token_supplier
    assert "api_key" not in captured and captured["max_retries"] == 0


@pytest.mark.parametrize(
    "schema,stage,valid",
    [
        (
            pytest.param(
                __import__("backend.app.agent.models", fromlist=["TicketAnalysis"]).TicketAnalysis,
                "analyze_ticket",
                {"issue_type": "vpn", "symptoms": "broken"},
                id="analysis",
            )
        ),
        (
            pytest.param(
                __import__(
                    "backend.app.agent.models", fromlist=["InvestigationDecision"]
                ).InvestigationDecision,
                "reason",
                {"evidence_summary": "Insufficient", "enough_information": False, "confidence": 0},
                id="decision",
            )
        ),
        (
            pytest.param(
                __import__(
                    "backend.app.agent.models", fromlist=["VerificationResult"]
                ).VerificationResult,
                "verify",
                {"confidence": 0, "should_continue_research": False, "justification": "No claims"},
                id="verification",
            )
        ),
    ],
)
@pytest.mark.asyncio
async def test_canonical_azure_stage_contracts(settings, schema, stage, valid):
    model, payloads = provider(settings, ["{}", json.dumps(valid)])
    output = await model.generate_structured(stage, {}, schema)
    assert isinstance(output, schema) and len(payloads) == 2
    assert payloads[0]["model"] == "test-deployment"


def test_azure_provider_closes_owned_client_and_credential(settings):
    from unittest.mock import Mock

    config = settings.model_copy(
        update={
            "azure_foundry_endpoint": "https://test.openai.azure.com",
            "azure_model_deployment": "test",
        }
    )
    client = Mock()
    credential = Mock()
    model = AzureOpenAIProvider(config, client)
    model.credential = credential
    model.close()
    client.close.assert_called_once()
    credential.close.assert_called_once()
