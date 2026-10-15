"""Regression coverage for bounded reasoning and recoverable provider failures."""

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from pydantic import SecretStr

from backend.app.agent.models import GeneralAnswer
from backend.app.models.context_budget import (
    bounded_context,
    estimate_request_tokens,
    model_context_window,
)
from backend.app.models.provider import LLMError
from backend.app.models.provider_azure_openai import AzureOpenAIProvider
from backend.app.models.provider_groq import GroqProvider
from backend.app.models.provider_routing import SelectableReasoningProvider, provider_scope


def groq_settings(settings, **updates):
    return settings.model_copy(
        update={
            "generation_provider": "groq",
            "groq_api_key": SecretStr("isolated-test-key"),
            "groq_model_name": "openai/gpt-oss-120b",
            "groq_context_window_tokens": 4096,
            "groq_max_output_tokens": 512,
            **updates,
        }
    )


def test_model_specific_budget_and_progressive_context_compaction(settings):
    assert model_context_window(groq_settings(settings)) == 4096
    evidence = [
        {
            "id": f"e{index}",
            "tool": "fetch_web_page",
            "source_system": "public_web",
            "source_type": "public_documentation",
            "timestamp": "2026-01-01T00:00:00Z",
            "reliability": 0.7,
            "url": f"https://example.com/{index}",
            "content": {"title": f"Source {index}", "text": "large result " * 4000},
        }
        for index in range(20)
    ]
    context = {
        "ticket": {"summary": "my vpn isnt working"},
        "conversation": [{"role": "user", "content": "old " * 4000}] * 12,
        "evidence": evidence,
        "contradictions": ["Keep this material contradiction"],
    }
    packed, report = bounded_context(context, 2200)
    assert packed["ticket"] == context["ticket"]
    assert packed["contradictions"] == context["contradictions"]
    assert len(packed["conversation"]) <= 4
    assert len(packed["evidence"]) <= 8
    assert estimate_request_tokens(packed) <= 2200
    assert report.compacted and report.original_tokens > report.final_tokens


class RecordingChat:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def ainvoke(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return AIMessage(
            content=reply,
            usage_metadata={"input_tokens": 40, "output_tokens": 8, "total_tokens": 48},
        )


@pytest.mark.asyncio
async def test_groq_enforces_context_budget_before_dispatch(settings):
    chat = RecordingChat(['{"answer":"Ask for the exact VPN error."}'])
    provider = GroqProvider(groq_settings(settings), chat=chat)
    result = await provider.generate_structured(
        "general_chat",
        {"message": "vpn", "evidence": [{"content": {"text": "x" * 100_000}}] * 12},
        GeneralAnswer,
    )
    assert result.answer.startswith("Ask")
    payload = json.dumps(chat.calls[0][0])
    assert len(payload) < 15_000
    assert chat.calls[0][1]["max_tokens"] == 512


@pytest.mark.asyncio
async def test_groq_context_overflow_retries_once_with_smaller_context(settings):
    overflow = RuntimeError("request too large")
    overflow.status_code = 400
    overflow.body = {"error": {"code": "context_length_exceeded"}}
    chat = RecordingChat([overflow, '{"answer":"Recovered safely."}'])
    provider = GroqProvider(groq_settings(settings), chat=chat)
    result = await provider.generate_structured(
        "general_chat", {"message": "vpn", "evidence": [{"content": {"text": "x" * 9000}}]}, GeneralAnswer
    )
    assert result.answer == "Recovered safely."
    assert len(chat.calls) == 2
    assert len(json.dumps(chat.calls[1][0])) < len(json.dumps(chat.calls[0][0]))


@pytest.mark.asyncio
async def test_invalid_groq_json_has_one_bounded_correction(settings):
    chat = RecordingChat(["not-json", '{"answer":"Corrected."}'])
    provider = GroqProvider(groq_settings(settings), chat=chat)
    assert (await provider.generate_structured("general_chat", {"message": "vpn"}, GeneralAnswer)).answer == "Corrected."
    assert len(chat.calls) == 2


def azure_provider(settings, error):
    def create(**kwargs):
        raise error

    config = settings.model_copy(
        update={
            "azure_foundry_endpoint": "https://example.openai.azure.com",
            "azure_model_deployment": "missing-deployment",
            "azure_usage_guard_enabled": True,
        }
    )
    return AzureOpenAIProvider(
        config,
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code,category,action",
    [
        (401, "Unauthorized", "authentication", "sign-in or credential"),
        (403, "Forbidden", "authorization", "role assignment"),
        (404, "DeploymentNotFound", "configuration", "deployment"),
        (429, "RateLimitReached", "capacity", "quota"),
    ],
)
async def test_azure_errors_are_sanitized_and_actionable(settings, status, code, category, action):
    error = RuntimeError("secret token and private response")
    error.status_code = status
    error.body = {"error": {"code": code}}
    model = azure_provider(settings, error)
    with pytest.raises(LLMError) as caught:
        await model.generate_structured("general_chat", {"private": "do not expose"}, GeneralAnswer)
    message = str(caught.value).lower()
    assert category in message and action in message
    assert "secret" not in message and "private" not in message


@pytest.mark.asyncio
async def test_optional_groq_to_azure_fallback_is_single_request_scoped_and_disclosed(
    settings, monkeypatch
):
    calls = []

    class Provider:
        def __init__(self, name):
            self.provider_name = name

        async def generate_structured(self, stage, context, schema):
            calls.append((self.provider_name, stage))
            if self.provider_name == "groq":
                raise LLMError("Groq unavailable", category="capacity", recoverable=True)
            return GeneralAnswer(answer="Azure fallback answer")

    monkeypatch.setattr(
        "backend.app.models.provider_factory.get_provider",
        lambda configured: Provider(configured.generation_provider),
    )
    config = groq_settings(
        settings,
        provider_fallback_enabled=True,
        azure_foundry_endpoint="https://example.openai.azure.com",
        azure_model_deployment="deployment",
    )
    router = SelectableReasoningProvider(config)
    events = []
    with provider_scope("groq", events.append):
        result = await router.generate_structured("general_chat", {}, GeneralAnswer)
        assert result.answer == "Azure fallback answer"
        await router.generate_structured("general_chat", {}, GeneralAnswer)
    assert calls == [
        ("groq", "general_chat"),
        ("azure_openai", "general_chat"),
        ("azure_openai", "general_chat"),
    ]
    assert any(event["kind"] == "provider_fallback" for event in events)


@pytest.mark.asyncio
async def test_fallback_disabled_never_constructs_azure(settings, monkeypatch):
    class FailedGroq:
        async def generate_structured(self, stage, context, schema):
            raise LLMError("Groq unavailable", category="capacity", recoverable=True)

    def factory(configured):
        if configured.generation_provider == "azure_openai":
            pytest.fail("Azure must not be constructed while fallback is disabled")
        return FailedGroq()

    monkeypatch.setattr("backend.app.models.provider_factory.get_provider", factory)
    router = SelectableReasoningProvider(groq_settings(settings, provider_fallback_enabled=False))
    with provider_scope("groq"):
        with pytest.raises(LLMError, match="Groq unavailable"):
            await router.generate_structured("general_chat", {}, GeneralAnswer)


def test_vpn_regression_returns_a_useful_clarification_not_generic_failure(make_client):
    client = make_client()
    response = client.post("/ask", json={"summary": "my vpn isnt working"})
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["status"] == "needs_input"
    question = result["pending_question"].casefold()
    assert any(term in question for term in ("error", "message", "device", "client", "when"))
    assert result["confidence"] == 0
