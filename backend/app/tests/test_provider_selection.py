"""Provider routing and real quota semantics, without external API calls."""

import asyncio
import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest
from backend.app.agent.models import GeneralAnswer
from backend.app.models.groq_limiter import GroqRateLimiter, duration_seconds
from backend.app.models.provider import LLMError
from backend.app.models.provider_factory import get_provider
from backend.app.models.provider_groq import GroqProvider
from backend.app.models.provider_routing import (
    SelectableReasoningProvider,
    provider_scope,
    selected_provider,
)
from langchain_core.messages import AIMessage
from pydantic import SecretStr


def configured(settings, **updates):
    return settings.model_copy(
        update={
            "generation_provider": "groq",
            "groq_api_key": SecretStr("isolated-key-never-live"),
            "groq_model_name": "isolated-model",
            "groq_queue_timeout_sec": 10,
            "groq_max_retries": 2,
            "groq_input_cad_per_million": None,
            "groq_output_cad_per_million": None,
            **updates,
        }
    )


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.waits = []

    def time(self):
        return self.now

    async def sleep(self, delay):
        self.waits.append(delay)
        self.now += delay
        await asyncio.sleep(0)


class Chat:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def ainvoke(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        response = self.replies.pop(0)
        if isinstance(response, BaseException):
            raise response
        return AIMessage(
            content=json.dumps(response),
            usage_metadata={"input_tokens": 25, "output_tokens": 12, "total_tokens": 37},
        )


def rate_limited(retry_after="2"):
    error = RuntimeError("secret API key must not be surfaced")
    error.status_code = 429
    error.response = SimpleNamespace(headers={"retry-after": retry_after})
    return error


def structured_rejected():
    error = RuntimeError("private failed generation must not be surfaced")
    error.status_code = 400
    error.response = SimpleNamespace(headers={})
    error.body = {
        "error": {
            "code": "json_validate_failed",
            "failed_generation": "private generated content",
        }
    }
    return error


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2m59.56s", 179.56),
        ("1h2m3s", 3723),
        ("250ms", 0.25),
        ("2.5", 2.5),
        ("-1", 0),
        ("nan", None),
        ("1s junk", None),
        ("", None),
    ],
)
def test_provider_duration_header_parser(value, expected):
    assert duration_seconds(value) == expected


@pytest.mark.asyncio
async def test_request_selection_isolated_between_concurrent_graphs_and_nested_tasks(settings):
    settings = configured(settings)

    async def graph(provider):
        with provider_scope(provider):
            await asyncio.sleep(0)

            async def child():
                await asyncio.sleep(0)
                return selected_provider(settings)

            return selected_provider(settings), await asyncio.create_task(child())

    assert await asyncio.gather(graph("groq"), graph("azure_openai"), graph("azure_gpt5_nano")) == [
        ("groq", "groq"),
        ("azure_openai", "azure_openai"),
        ("azure_gpt5_nano", "azure_gpt5_nano"),
    ]
    assert selected_provider(settings) == "groq"


@pytest.mark.asyncio
async def test_common_langchain_runnable_routes_every_stage_and_preserves_no_fallback(
    settings, monkeypatch
):
    routed = []

    class Provider:
        def __init__(self, name):
            self.name = name

        async def generate_structured(self, stage, context, schema):
            routed.append((self.name, stage))
            return GeneralAnswer(answer="Supported")

    monkeypatch.setattr(
        "backend.app.models.provider_factory.get_provider",
        lambda settings: Provider(settings.generation_provider),
    )
    router = SelectableReasoningProvider(configured(settings))
    for name in ("groq", "azure_openai", "azure_gpt5_nano"):
        with provider_scope(name):
            for stage in ("classify_intent", "plan", "reason", "verify", "general_chat"):
                assert (
                    await router.generate_structured(stage, {}, GeneralAnswer)
                ).answer == "Supported"
    assert routed == [
        (name, stage)
        for name in ("groq", "azure_openai", "azure_gpt5_nano")
        for stage in ("classify_intent", "plan", "reason", "verify", "general_chat")
    ]
    assert set(router._providers) == {"groq", "azure_openai", "azure_gpt5_nano"}


def test_nano_readiness_requires_independent_deployment(settings):
    router = SelectableReasoningProvider(
        configured(
            settings,
            generation_provider="azure_gpt5_nano",
            azure_foundry_endpoint="https://example.openai.azure.com",
            azure_model_deployment="regular-deployment",
            azure_gpt5_nano_deployment="",
        )
    )
    assert router.configuration() == {
        "provider": "azure_gpt5_nano",
        "configured": False,
        "missing": ["AZURE_GPT5_NANO_DEPLOYMENT"],
    }


@pytest.mark.asyncio
async def test_missing_groq_does_not_use_azure_or_prevent_readiness(settings, monkeypatch):
    router = SelectableReasoningProvider(configured(settings, groq_api_key=None))
    assert router.configuration() == {
        "provider": "groq",
        "configured": False,
        "missing": ["GROQ_API_KEY"],
    }
    monkeypatch.setattr(
        "backend.app.models.provider_factory.AzureOpenAIProvider",
        lambda *_: pytest.fail("Unexpected Azure fallback"),
    )
    with pytest.raises(LLMError, match="Groq is not configured"):
        await router.generate_structured("general_chat", {}, GeneralAnswer)
    assert not router._providers


@pytest.mark.asyncio
async def test_daily_header_is_not_misinterpreted_as_requests_per_minute(settings):
    clock = FakeClock()
    limiter = GroqRateLimiter(
        configured(settings, groq_queue_timeout_sec=180), clock=clock.time, sleeper=clock.sleep
    )
    await limiter.observe(
        {
            "x-ratelimit-limit-requests": "1000",
            "x-ratelimit-remaining-requests": "0",
            "x-ratelimit-reset-requests": "23h",
        }
    )
    with pytest.raises(LLMError, match="account limit to reset"):
        async with limiter.slot(30, "plan"):
            pytest.fail("Exhausted daily limit dispatched a request")
    assert limiter.budgets["rpd"].period == 86400
    assert not clock.waits


@pytest.mark.asyncio
async def test_minute_tokens_wait_for_actual_header_reset_and_emit_waiting(settings):
    clock = FakeClock()
    events = []
    limiter = GroqRateLimiter(
        configured(settings, groq_queue_timeout_sec=20), clock=clock.time, sleeper=clock.sleep
    )
    await limiter.observe(
        {
            "x-ratelimit-limit-tokens": "1000",
            "x-ratelimit-remaining-tokens": "5",
            "x-ratelimit-reset-tokens": "7.66s",
        }
    )
    with provider_scope("groq", events.append):
        async with limiter.slot(100, "plan"):
            pass
    assert clock.waits == [pytest.approx(7.66)]
    assert [event["status"] for event in events] == ["waiting", "running"]


@pytest.mark.asyncio
async def test_configured_rpm_and_tpd_are_separate_and_usage_settles(settings):
    clock = FakeClock()
    limiter = GroqRateLimiter(
        configured(
            settings, groq_requests_per_minute=1, groq_tokens_per_day=100, groq_queue_timeout_sec=65
        ),
        clock=clock.time,
        sleeper=clock.sleep,
    )
    async with limiter.slot(80, "reason") as reservation:
        await limiter.settle(reservation, 20)
    async with limiter.slot(80, "verify"):
        pass
    assert clock.waits == [60]
    assert sum(tokens for _, tokens, _ in limiter.history) == 100
    with pytest.raises(LLMError, match="capacity is exhausted"):
        async with limiter.slot(1, "reason"):
            pass


@pytest.mark.asyncio
async def test_unknown_capacity_is_serialized_and_queue_cancel_releases_slot(settings):
    limiter = GroqRateLimiter(configured(settings))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def first():
        async with limiter.slot(10, "plan"):
            entered.set()
            await release.wait()

    async def waiting():
        async with limiter.slot(10, "reason"):
            pytest.fail("Unknown concurrency allowed overlapping calls")

    task = asyncio.create_task(first())
    await entered.wait()
    queued = asyncio.create_task(waiting())
    await asyncio.sleep(0.01)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    release.set()
    await task
    async with limiter.slot(10, "verify"):
        pass


@pytest.mark.asyncio
async def test_429_retries_only_model_invocation_and_tracks_usage_without_private_text(settings):
    config = configured(settings)
    clock = FakeClock()
    limiter = GroqRateLimiter(config, clock=clock.time, sleeper=clock.sleep)
    chat = Chat([rate_limited("2"), {"answer": "Evidence supports these next steps."}])
    provider = GroqProvider(config, chat=chat, limiter=limiter)
    result = await provider.generate_structured(
        "general_chat", {"private_question": "Do not store private question"}, GeneralAnswer
    )
    assert result.answer.startswith("Evidence")
    assert len(chat.calls) == 2 and clock.waits[0] >= 2
    assert chat.calls[0][1]["response_format"] == {"type": "json_object"}
    path = config.case_db_path.with_name("llm-provider-usage.sqlite3")
    with closing(sqlite3.connect(path)) as connection:
        rows = connection.execute(
            "SELECT provider,input_tokens,output_tokens,outcome,estimate_cad "
            "FROM llm_usage ORDER BY created"
        ).fetchall()
    assert rows == [("groq", None, None, "rate_limited", None), ("groq", 25, 12, "completed", None)]
    assert b"Do not store private question" not in path.read_bytes()


@pytest.mark.asyncio
async def test_429_retries_are_bounded_and_secrets_are_not_exposed(settings):
    config = configured(settings)
    clock = FakeClock()
    provider = GroqProvider(
        config,
        chat=Chat([rate_limited("0")] * 3),
        limiter=GroqRateLimiter(config, clock=clock.time, sleeper=clock.sleep),
    )
    with pytest.raises(LLMError, match="Groq is rate limited") as error:
        await provider.generate_structured("reason", {}, GeneralAnswer)
    assert len(provider.chat.calls) == 3
    assert "secret" not in str(error.value)
    assert len(clock.waits) == 2


@pytest.mark.asyncio
async def test_ambiguous_failure_never_retries_or_uses_another_provider(settings):
    provider = GroqProvider(configured(settings), chat=Chat([TimeoutError("raw credentials")]))
    with pytest.raises(LLMError, match="no other model was used") as error:
        await provider.generate_structured("reason", {}, GeneralAnswer)
    assert len(provider.chat.calls) == 1
    assert "credentials" not in str(error.value)


@pytest.mark.asyncio
async def test_invalid_structured_response_cannot_dispatch_tools(settings):
    provider = GroqProvider(
        configured(settings),
        chat=Chat(
            [
                {"untrusted": "call destructive tool"},
                {"untrusted": "call destructive tool again"},
            ]
        ),
    )
    with pytest.raises(LLMError, match="invalid structured GeneralAnswer"):
        await provider.generate_structured("reason", {}, GeneralAnswer)
    assert len(provider.chat.calls) == 2


@pytest.mark.asyncio
async def test_provider_rejected_structured_output_retries_once_without_leaking_content(settings):
    provider = GroqProvider(
        configured(settings),
        chat=Chat([structured_rejected(), {"answer": "Validated on the bounded retry."}]),
    )
    result = await provider.generate_structured("general_chat", {}, GeneralAnswer)
    assert result.answer == "Validated on the bounded retry."
    assert len(provider.chat.calls) == 2
    path = provider.settings.case_db_path.with_name("llm-provider-usage.sqlite3")
    assert b"private generated content" not in path.read_bytes()


def test_factory_keeps_test_stub_exclusive_to_test_mode(settings):
    with pytest.raises(ValueError, match="Only configured"):
        get_provider(settings.model_copy(update={"test_mode": False}))


@pytest.mark.asyncio
async def test_real_langchain_groq_sdk_json_mode_and_header_hook(settings, monkeypatch):
    """Exercise real LangChain and Groq serialization, without network/inference."""
    import httpx

    requests = []

    async def respond(request):
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={
                "x-ratelimit-limit-requests": "1000",
                "x-ratelimit-remaining-requests": "999",
                "x-ratelimit-reset-requests": "12h",
                "x-ratelimit-limit-tokens": "5000",
                "x-ratelimit-remaining-tokens": "4963",
                "x-ratelimit-reset-tokens": "30s",
            },
            json={
                "id": "isolated-completion",
                "object": "chat.completion",
                "created": 1,
                "model": "isolated-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"answer":"Check the verified evidence."}',
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 25, "completion_tokens": 12, "total_tokens": 37},
            },
        )

    original = httpx.AsyncClient

    class IsolatedClient(original):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", IsolatedClient)
    provider = GroqProvider(configured(settings, groq_model_name="openai/gpt-oss-20b"))
    try:
        result = await provider.generate_structured("general_chat", {}, GeneralAnswer)
        assert result.answer == "Check the verified evidence."
        response_format = requests[0]["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["name"] == "GeneralAnswer"
        assert response_format["json_schema"]["strict"] is True
        assert requests[0]["model"] == "openai/gpt-oss-20b"
        assert requests[0]["include_reasoning"] is False
        assert requests[0]["store"] is False
        assert requests[0]["reasoning_effort"] == "low"
        assert requests[0].get("reasoning_format") is None
        assert provider.limiter.budgets["rpd"].remaining == 999
        assert provider.limiter.budgets["tpm"].remaining == 4963
        assert provider.chat.max_retries == 0
    finally:
        await provider.aclose()
