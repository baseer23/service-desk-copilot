import json
from types import SimpleNamespace

import pytest
from backend.app.agent.models import InvestigationPlan, RequestIntent
from backend.app.models.provider import LLMError
from backend.app.models.provider_azure_openai import AzureOpenAIProvider
from pydantic import BaseModel


class Probe(BaseModel):
    status: str


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
async def test_overlapping_structured_calls_queue_without_reservation_conflict(settings):
    import asyncio
    import threading

    entered, release = threading.Event(), threading.Event()
    calls = []
    config = settings.model_copy(
        update={
            "azure_foundry_endpoint": "https://example.openai.azure.com",
            "azure_model_deployment": "test-deployment",
        }
    )

    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            entered.set()
            assert release.wait(2)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"status":"ok"}'))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10),
        )

    model = AzureOpenAIProvider(
        config, SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    )
    first = asyncio.create_task(model.generate_structured("readiness", {}, Probe))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        second = asyncio.create_task(model.generate_structured("readiness", {}, Probe))
        await asyncio.sleep(0.02)
        assert len(calls) == 1 and not second.done()
        release.set()
        results = await asyncio.gather(first, second)
        assert len(calls) == 2 and all(result.status == "ok" for result in results)
    finally:
        release.set()
        await first


@pytest.mark.asyncio
async def test_readiness_probe_is_one_minimal_request_without_schema_retries(settings):
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
async def test_azure_dispatch_has_no_application_financial_gate(settings):
    model, payloads = provider(settings, ['{"status":"ok"}'])
    result = await model.generate_structured("readiness", {}, Probe)
    assert result.status == "ok" and len(payloads) == 1


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


@pytest.mark.asyncio
async def test_gpt5_nano_uses_independent_deployment_without_unsupported_temperature(settings):
    from backend.app.models.provider_factory import get_provider

    payloads = []

    def create(**kwargs):
        payloads.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"status":"ok"}'))],
            usage=None,
        )

    config = settings.model_copy(
        update={
            "generation_provider": "azure_gpt5_nano",
            "azure_foundry_endpoint": "https://example.openai.azure.com",
            "azure_model_deployment": "regular-deployment",
            "azure_gpt5_nano_deployment": "nano-deployment",
        }
    )
    model = get_provider(config)
    model.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    result = await model.generate_structured("readiness", {}, Probe)

    assert result.status == "ok"
    assert model.name() == "azure_gpt5_nano"
    assert payloads[0]["model"] == "nano-deployment"
    assert "temperature" not in payloads[0]
    assert payloads[0]["max_completion_tokens"] == 80


def test_gpt5_nano_requires_its_own_deployment(settings):
    from backend.app.models.provider_factory import get_provider

    config = settings.model_copy(
        update={
            "generation_provider": "azure_gpt5_nano",
            "azure_foundry_endpoint": "https://example.openai.azure.com",
            "azure_model_deployment": "regular-deployment",
            "azure_gpt5_nano_deployment": "",
        }
    )
    with pytest.raises(LLMError, match="endpoint and deployment"):
        get_provider(config)


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


def test_schema_sent_once_context_compaction_preserves_false_and_evidence(settings):
    from backend.app.models.provider_azure_openai import structured_request

    evidence = [
        {"id": "e1", "source_system": "local_fixture", "content": {"confirmed": False, "count": 0}}
    ]
    messages, response_format = structured_request(
        settings,
        "classify_intent",
        {
            "request": "Fictional MFA failure",
            "evidence": evidence,
            "empty": [],
            "missing": None,
        },
        RequestIntent,
    )
    assert "Required JSON schema" not in messages[0]["content"]
    assert '"title"' not in json.dumps(response_format)
    assert response_format["json_schema"]["schema"]["additionalProperties"] is False
    context = json.loads(messages[1]["content"])
    assert context["evidence"] == evidence and "empty" not in context and "missing" not in context
    messages, response_format = structured_request(settings, "plan", {}, InvestigationPlan)
    assert response_format == {"type": "json_object"}
    assert "Required JSON schema" in messages[0]["content"]


def test_schema_compaction_keeps_real_fields_named_like_schema_metadata():
    from backend.app.models.provider_azure_openai import compact_schema, strict_schema
    from pydantic import BaseModel

    class NamedFields(BaseModel):
        title: str
        description: str
        default: int

    schema = strict_schema(compact_schema(NamedFields.model_json_schema()))
    assert set(schema["properties"]) == {"title", "description", "default"}
    assert set(schema["required"]) == {"title", "description", "default"}
