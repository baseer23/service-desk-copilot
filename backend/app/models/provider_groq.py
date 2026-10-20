"""LangChain Groq reasoning with a shared queue and no provider fallback."""

import asyncio
import os
import random
import sqlite3
import time
import uuid
from contextlib import closing
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from backend.app.core.config import Settings

from .context_budget import bounded_context, estimate_request_tokens, model_context_window
from .groq_limiter import GroqRateLimiter
from .provider import LLMError
from .provider_azure_openai import TOKEN_LIMITS, structured_request
from .provider_routing import model_event

T = TypeVar("T", bound=BaseModel)


def _schema_feedback(error: Exception) -> str:
    """Return bounded schema-only feedback without model content or user data."""
    if not isinstance(error, ValidationError):
        return "The response was not a valid JSON object for the required schema."
    issues = []
    for item in error.errors(include_input=False, include_context=False)[:8]:
        location = ".".join(str(part) for part in item.get("loc", ())) or "root"
        issues.append(f"{location}: {item.get('type', 'invalid')}")
    return "Schema errors: " + "; ".join(issues)


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
        # GPT-OSS supports provider-enforced strict JSON Schema on Groq. Other
        # configured models retain JSON Object mode unless explicitly supported.
        strict_models = {"openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"}
        config = self.settings.model_copy(
            update={"azure_structured_outputs": self.settings.groq_model_name in strict_models}
        )
        output_bound = min(TOKEN_LIMITS.get(stage, 1200), self.settings.groq_max_output_tokens)
        window = model_context_window(self.settings)
        account_token_capacity = self.limiter.token_capacity()
        if account_token_capacity:
            window = min(window, account_token_capacity)

        def prepare(source: dict, fraction: float = 1.0):
            # Reserve output, protocol overhead, instructions and schema. The
            # packed context is a transient view; persisted evidence is untouched.
            context_budget = max(256, int((window - output_bound - 1536) * fraction))
            packed, report = bounded_context(source, context_budget)
            messages, response_format = structured_request(config, stage, packed, schema)
            estimated = (
                estimate_request_tokens({"messages": messages, "response_format": response_format})
                + output_bound
                + 128
            )
            if estimated > window:
                packed, report = bounded_context(packed, max(128, context_budget // 2))
                messages, response_format = structured_request(config, stage, packed, schema)
                estimated = (
                    estimate_request_tokens(
                        {"messages": messages, "response_format": response_format}
                    )
                    + output_bound
                    + 128
                )
            if estimated > window:
                raise LLMError(
                    "Groq request cannot fit the configured model context window",
                    category="context_length",
                )
            return packed, report, messages, response_format, estimated

        packed, report, messages, response_format, estimated_tokens = prepare(context)
        if report.compacted:
            await model_event(
                kind="context_compaction",
                status="completed",
                provider="groq",
                stage=stage,
                summary="Model context compacted while retaining source references",
                original_tokens=report.original_tokens,
                final_tokens=report.final_tokens,
            )
        rate_attempt = 0
        overflow_attempt = 0
        validation_attempt = 0
        while True:
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
                            fallback_delay=(2**rate_attempt)
                            + random.SystemRandom().uniform(0, 0.5),
                        )
                        await asyncio.to_thread(
                            self._record_usage,
                            stage,
                            None,
                            time.monotonic() - started,
                            "rate_limited",
                        )
                        if rate_attempt < self.settings.groq_max_retries:
                            rate_attempt += 1
                            continue
                        await model_event(
                            kind="llm_capacity",
                            status="failed",
                            provider="groq",
                            stage=stage,
                            summary="Groq is rate limited; retry after the reset",
                        )
                        raise LLMError(
                            "Groq is rate limited; wait for capacity and try again",
                            category="capacity",
                            recoverable=True,
                        ) from None
                    body = getattr(exc, "body", None)
                    detail = body.get("error", body) if isinstance(body, dict) else {}
                    error_code = detail.get("code") if isinstance(detail, dict) else None
                    if (
                        status == 400
                        and error_code == "json_validate_failed"
                        and validation_attempt == 0
                    ):
                        validation_attempt += 1
                        # The same Groq model may fail to satisfy provider-side
                        # strict generation for a valid schema. Retry once in
                        # JSON-object mode and retain local Pydantic validation;
                        # this is not a provider/model fallback.
                        config = config.model_copy(update={"azure_structured_outputs": False})
                        packed = {
                            **packed,
                            "response_correction": (
                                "The prior response failed the required JSON schema. Return one complete "
                                "object matching every field and the conversation interpretation rules. "
                                "Required top-level fields: "
                                + ", ".join(schema.model_fields)
                                + "."
                            ),
                        }
                        packed, report, messages, response_format, estimated_tokens = prepare(
                            packed, 0.75
                        )
                        await asyncio.to_thread(
                            self._record_usage,
                            stage,
                            None,
                            time.monotonic() - started,
                            "structured_recovery",
                        )
                        await model_event(
                            kind="structured_recovery",
                            status="running",
                            provider="groq",
                            stage=stage,
                            summary="Retrying one provider-rejected structured response",
                        )
                        continue
                    if (
                        status == 400
                        and error_code in {"context_length_exceeded", "context_window_exceeded"}
                        and overflow_attempt == 0
                    ):
                        overflow_attempt += 1
                        packed, report, messages, response_format, estimated_tokens = prepare(
                            packed, 0.45
                        )
                        await asyncio.to_thread(
                            self._record_usage,
                            stage,
                            None,
                            time.monotonic() - started,
                            "context_recovery",
                        )
                        await model_event(
                            kind="context_compaction",
                            status="completed",
                            provider="groq",
                            stage=stage,
                            summary="Groq rejected the context; retrying once with a smaller evidence view",
                            final_tokens=report.final_tokens,
                        )
                        continue
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
                        f"Groq request failed [{code}]; no other model was used",
                        category=code,
                        recoverable=status in {498, 502, 503},
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
                except (ValidationError, ValueError, TypeError) as exc:
                    if validation_attempt == 0:
                        validation_attempt += 1
                        packed = {
                            **packed,
                            "response_correction": (
                                _schema_feedback(exc)
                                + " Return one complete JSON object with every required field and "
                                "no prose. Use only the exact supplied evidence identifiers."
                            ),
                        }
                        packed, report, messages, response_format, estimated_tokens = prepare(
                            packed, 0.75
                        )
                        await model_event(
                            kind="structured_recovery",
                            status="running",
                            provider="groq",
                            stage=stage,
                            summary="Retrying one malformed model response with a smaller request",
                        )
                        continue
                    raise LLMError(
                        "Groq returned invalid structured " + schema.__name__,
                        category="structured_output",
                        recoverable=True,
                    ) from None
                await model_event(
                    kind="llm_capacity",
                    status="completed",
                    provider="groq",
                    stage=stage,
                    summary="Groq request completed",
                )
                return value

    async def aclose(self):
        if self.http_client:
            await self.http_client.aclose()
