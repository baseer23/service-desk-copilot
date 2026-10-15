"""LangChain Groq reasoning with a shared queue and no provider fallback."""

import asyncio
import json
import math
import os
import random
import sqlite3
import time
import uuid
from contextlib import closing
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from backend.app.core.config import Settings

from .groq_limiter import GroqRateLimiter
from .provider import LLMError
from .provider_azure_openai import TOKEN_LIMITS, structured_request
from .provider_routing import model_event

T = TypeVar("T", bound=BaseModel)


class GroqProvider:
    def __init__(
        self, settings: Settings, chat: Any = None, limiter: GroqRateLimiter | None = None
    ):
        if not settings.groq_api_key or not settings.groq_model_name:
            raise LLMError(
                "Groq is not configured; an administrator must configure its model and API key"
            )
        self.settings = settings
        self.limiter = limiter or GroqRateLimiter(settings)
        self.http_client = None
        if chat is not None:
            self.chat = chat
            return
        try:
            import httpx
            from langchain_groq import ChatGroq

            async def observe_headers(response):
                await self.limiter.observe(response.headers)

            self.http_client = httpx.AsyncClient(
                timeout=settings.model_timeout_sec,
                event_hooks={"response": [observe_headers]},
            )
            reasoning_options = (
                {
                    "reasoning_effort": "low",
                    "model_kwargs": {"include_reasoning": False, "store": False},
                }
                if settings.groq_model_name.startswith("openai/gpt-oss-")
                else {"model_kwargs": {"store": False}}
            )
            self.chat = ChatGroq(
                api_key=settings.groq_api_key.get_secret_value(),
                model=settings.groq_model_name,
                # The SDK includes /openai/v1 in completion resource paths.
                base_url="https://api.groq.com",
                temperature=0,
                timeout=settings.model_timeout_sec,
                max_retries=0,
                http_async_client=self.http_client,
                **reasoning_options,
            )
        except Exception:  # noqa: BLE001 - never leak SDK credentials at this boundary
            raise LLMError("Groq initialization failed; check the configured integration") from None

    def name(self) -> str:
        return "groq"

    def as_runnable(self):
        from langchain_core.runnables import RunnableLambda

        async def invoke(request):
            return await self.generate_structured(
                request["stage"], request["context"], request["schema"]
            )

        return RunnableLambda(invoke, name="Groq structured reasoning")

    def _record_usage(
        self, stage: str, usage: dict[str, int] | None, elapsed: float, outcome: str
    ) -> None:
        """Persist numeric counters only: no prompts, identities or generated text."""
        path = self.settings.case_db_path.with_name("llm-provider-usage.sqlite3")
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
            os.close(fd)
            with closing(sqlite3.connect(path, timeout=5)) as connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS llm_usage "
                    "(id TEXT PRIMARY KEY, created REAL NOT NULL, provider TEXT NOT NULL, "
                    "model TEXT NOT NULL, "
                    "stage TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER, "
                    "duration REAL NOT NULL, outcome TEXT NOT NULL, estimate_cad REAL)"
                )
                input_tokens = usage.get("input_tokens") if usage else None
                output_tokens = usage.get("output_tokens") if usage else None
                # Account plans vary. An unknown price stays unknown, never 'free'.
                input_rate = getattr(self.settings, "groq_input_cad_per_million", None)
                output_rate = getattr(self.settings, "groq_output_cad_per_million", None)
                estimate = (
                    (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000
                    if input_tokens is not None
                    and output_tokens is not None
                    and input_rate is not None
                    and output_rate is not None
                    else None
                )
                connection.execute(
                    "INSERT INTO llm_usage VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        uuid.uuid4().hex,
                        time.time(),
                        "groq",
                        self.settings.groq_model_name,
                        stage,
                        input_tokens,
                        output_tokens,
                        elapsed,
                        outcome,
                        estimate,
                    ),
                )
                connection.commit()
        except (OSError, sqlite3.Error):
            raise LLMError("Model usage accounting is unavailable; check local storage") from None

    async def generate_structured(self, stage: str, context: dict, schema: type[T]) -> T:
        # JSON mode works with free-form MCP argument maps, unlike strict schemas.
        config = self.settings.model_copy(update={"azure_structured_outputs": False})
        messages, response_format = structured_request(config, stage, context, schema)
        output_bound = min(TOKEN_LIMITS.get(stage, 1200), self.settings.groq_max_output_tokens)
        estimated_tokens = (
            math.ceil(len(json.dumps(messages, ensure_ascii=False).encode()) / 3)
            + output_bound
            + 128
        )
        for attempt in range(self.settings.groq_max_retries + 1):
            async with self.limiter.slot(estimated_tokens, stage) as reservation:
                started = time.monotonic()
                try:
                    # Only this model invocation can retry. Graph/MCP actions
                    # execute after successful schema validation, once.
                    response = await asyncio.wait_for(
                        self.chat.ainvoke(
                            messages, response_format=response_format, max_tokens=output_bound
                        ),
                        timeout=self.settings.model_timeout_sec,
                    )
                except asyncio.CancelledError:
                    await asyncio.shield(
                        asyncio.to_thread(
                            self._record_usage,
                            stage,
                            None,
                            time.monotonic() - started,
                            "cancelled_unknown",
                        )
                    )
                    raise
                except Exception as exc:  # noqa: BLE001 - sanitized provider boundary
                    status = getattr(exc, "status_code", None)
                    headers = getattr(getattr(exc, "response", None), "headers", {})
                    if status == 429:
                        await self.limiter.observe(
                            headers,
                            retry=True,
                            fallback_delay=(2**attempt) + random.SystemRandom().uniform(0, 0.5),
                        )
                        await asyncio.to_thread(
                            self._record_usage,
                            stage,
                            None,
                            time.monotonic() - started,
                            "rate_limited",
                        )
                        if attempt < self.settings.groq_max_retries:
                            continue
                        await model_event(
                            kind="llm_capacity",
                            status="failed",
                            provider="groq",
                            stage=stage,
                            summary="Groq is rate limited; retry after the reset",
                        )
                        raise LLMError(
                            "Groq is rate limited; wait for capacity and try again"
                        ) from None
                    await asyncio.to_thread(
                        self._record_usage, stage, None, time.monotonic() - started, "failed"
                    )
                    await model_event(
                        kind="llm_capacity",
                        status="failed",
                        provider="groq",
                        stage=stage,
                        summary="Groq could not complete the request",
                    )
                    # Transport failures may already be billable. Never blindly
                    # retry a timeout, connection loss or other ambiguous error.
                    code = (
                        "authentication"
                        if status in {401, 403}
                        else (
                            f"http_{status}"
                            if isinstance(status, int) and 400 <= status <= 599
                            else "unavailable"
                        )
                    )
                    raise LLMError(
                        f"Groq request failed [{code}]; no other model was used"
                    ) from None
                usage = getattr(response, "usage_metadata", None)
                if usage is None:
                    counters = getattr(response, "response_metadata", {}).get("token_usage", {})
                    if {"prompt_tokens", "completion_tokens"} <= counters.keys():
                        usage = {
                            "input_tokens": counters["prompt_tokens"],
                            "output_tokens": counters["completion_tokens"],
                            "total_tokens": counters.get(
                                "total_tokens",
                                counters["prompt_tokens"] + counters["completion_tokens"],
                            ),
                        }
                if usage:
                    usage = {
                        key: value
                        for key, value in usage.items()
                        if key in {"input_tokens", "output_tokens", "total_tokens"}
                        and isinstance(value, int)
                        and not isinstance(value, bool)
                        and value >= 0
                    }
                if usage and isinstance(usage.get("total_tokens"), int):
                    await self.limiter.settle(reservation, usage["total_tokens"])
                await asyncio.to_thread(
                    self._record_usage, stage, usage, time.monotonic() - started, "completed"
                )
                await model_event(
                    kind="llm_usage",
                    status="completed",
                    provider="groq",
                    stage=stage,
                    summary="Groq request usage recorded",
                    usage=usage,
                )
                try:
                    value = schema.model_validate_json(response.content)
                except (ValidationError, ValueError, TypeError):
                    raise LLMError("Groq returned invalid structured " + schema.__name__) from None
                await model_event(
                    kind="llm_capacity",
                    status="completed",
                    provider="groq",
                    stage=stage,
                    summary="Groq request completed",
                )
                return value
        raise LLMError("Groq capacity is unavailable")

    async def aclose(self):
        if self.http_client:
            await self.http_client.aclose()
