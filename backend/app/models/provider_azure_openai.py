"""Azure case reasoning, using Entra ID by default and bounded validation."""

import asyncio
import json
import re
import time
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from backend.app.core.azure_errors import azure_error_code
from backend.app.core.config import Settings

from .instructions import stage_instructions
from .context_budget import bounded_context, estimate_request_tokens
from .provider import LLMError
from .provider_routing import model_event

T = TypeVar("T", bound=BaseModel)
TOKEN_LIMITS = {
    "readiness": 80,
    "classify_intent": 220,
    "general_chat": 1500,
    "analyze_ticket": 600,
    "plan": 1400,
    "reason": 1500,
    "verify": 800,
    "evaluate_sources": 350,
}


def azure_failure(exc: Exception) -> tuple[str, str, bool, str | None]:
    """Return a sanitized category, action, retryability and request identifier."""
    status = getattr(exc, "status_code", getattr(exc, "status", None))
    code = azure_error_code(exc)
    folded = code.casefold()
    authentication_error = type(exc).__name__ in {
        "AuthenticationError", "ClientAuthenticationError", "CredentialUnavailableError"
    }
    cause = getattr(exc, "__cause__", None)
    if cause is not None and type(cause).__name__ in {
        "AuthenticationError", "ClientAuthenticationError", "CredentialUnavailableError"
    }:
        authentication_error = True
    if status == 401 or authentication_error:
        category, action, recoverable = (
            "authentication",
            "Verify the Entra sign-in or credential configured for Azure OpenAI.",
            False,
        )
    elif status == 403:
        category, action, recoverable = (
            "authorization",
            "Verify the Azure OpenAI role assignment for this identity and resource.",
            False,
        )
    elif status == 404 or "deployment" in folded or "model_not_found" in folded:
        category, action, recoverable = (
            "configuration",
            "Verify the Azure resource endpoint, deployment name and API version.",
            False,
        )
    elif status == 429 or any(word in folded for word in ("quota", "ratelimit", "capacity")):
        category, action, recoverable = (
            "capacity",
            "Azure quota or rate capacity is unavailable; retry after the service reset.",
            True,
        )
    elif any(word in folded for word in ("context", "tokenlimit", "max_tokens")):
        category, action, recoverable = (
            "context_length",
            "The request exceeded the deployment context limit; retry with less evidence.",
            True,
        )
    elif isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        category, action, recoverable = (
            "timeout",
            "Azure did not finish before the timeout; retry when the service is responsive.",
            True,
        )
    elif isinstance(status, int) and status >= 500:
        category, action, recoverable = (
            "service_unavailable",
            "Azure is temporarily unavailable; retry later.",
            True,
        )
    else:
        category, action, recoverable = (
            "request_failed",
            "Verify the deployment configuration and Azure service status.",
            False,
        )
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    request_id = headers.get("x-request-id") or headers.get("apim-request-id")
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_id):
        request_id = None
    return category, action, recoverable, request_id


def compact_schema(value: Any) -> Any:
    """Remove display-only schema metadata; preserve constraints and field names."""
    if isinstance(value, list):
        return [compact_schema(item) for item in value]
    if isinstance(value, dict):
        return {
            key: (
                {name: compact_schema(child) for name, child in item.items()}
                if key in {"properties", "$defs", "definitions"}
                else compact_schema(item)
            )
            for key, item in value.items()
            if key not in {"title", "description", "examples", "default"}
        }
    return value


def compact_context(value: Any) -> Any:
    """Omit empty context, retaining false/zero and all actual evidence/provenance."""
    if isinstance(value, list):
        return [compact_context(item) for item in value]
    if isinstance(value, dict):
        return {
            key: compact_context(item)
            for key, item in value.items()
            if item is not None and item != [] and item != {} and item != ""
        }
    return value


def structured_request(
    settings: Settings, stage: str, context: dict, schema: type[BaseModel]
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Construct requests once for execution and offline throughput measurement."""
    instructions = stage_instructions(stage, context)
    raw_schema = compact_schema(schema.model_json_schema())
    supported = strict_schema(raw_schema) if settings.azure_structured_outputs else None
    response_format: dict[str, Any]
    if supported:
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "strict": True,
                "schema": supported,
            },
        }
        # The response format already supplies the schema. Duplicating it in a
        # system message wastes quota and still does not strengthen validation.
        instructions += "\nReturn the required JSON object. Keep explanations concise."
    else:
        response_format = {"type": "json_object"}
        instructions += "\nRequired JSON schema:\n" + json.dumps(raw_schema, separators=(",", ":"))
    messages = [
        {"role": "system", "content": instructions},
        {
            "role": "user",
            "content": json.dumps(
                compact_context(context),
                default=str,
                separators=(",", ":"),
            ),
        },
    ]
    return messages, response_format


def strict_schema(value: dict[str, Any]) -> dict[str, Any] | None:
    """Azure strict schemas cannot represent our unconstrained tool-argument maps."""
    schema = json.loads(json.dumps(value))

    def convert(node: Any) -> bool:
        if isinstance(node, list):
            return all(convert(item) for item in node)
        if not isinstance(node, dict):
            return True
        node.pop("default", None)
        if node.get("type") == "object":
            if "properties" not in node:
                return False
            node["additionalProperties"] = False
            node["required"] = list(node["properties"])
        for key in (
            "minimum",
            "maximum",
            "minLength",
            "maxLength",
            "minItems",
            "maxItems",
            "format",
        ):
            node.pop(key, None)
        return all(
            (
                all(convert(child) for child in item.values())
                if key in {"properties", "$defs", "definitions"}
                else convert(item)
            )
            for key, item in node.items()
        )

    return schema if convert(schema) else None


class AzureOpenAIProvider:
    def __init__(
        self,
        settings: Settings,
        client: Any = None,
        *,
        deployment: str | None = None,
        provider_name: str = "azure_openai",
        reasoning_model: bool = False,
    ) -> None:
        self.deployment = deployment if deployment is not None else settings.azure_model_deployment
        self.provider_name = provider_name
        self.reasoning_model = reasoning_model
        if not settings.azure_foundry_endpoint or not self.deployment:
            raise LLMError("Azure OpenAI endpoint and deployment must be configured")
        self.settings = settings
        self.request_lock = asyncio.Lock()
        self.next_request_at = 0.0
        self.credential: Any = None
        if client is not None:
            self.client = client
            return
        try:
            from openai import AzureOpenAI

            options: dict[str, Any] = {
                "azure_endpoint": settings.azure_foundry_endpoint,
                "api_version": settings.azure_openai_api_version,
                "timeout": settings.model_timeout_sec,
                "max_retries": 0,
            }
            if settings.azure_use_entra_id:
                from azure.identity import (
                    AzureCliCredential,
                    DefaultAzureCredential,
                    get_bearer_token_provider,
                )

                self.credential = (
                    AzureCliCredential(process_timeout=5)
                    if settings.deployment_mode == "local"
                    else DefaultAzureCredential(
                        process_timeout=5, connection_timeout=5, read_timeout=5, retry_total=0
                    )
                )
                options["azure_ad_token_provider"] = get_bearer_token_provider(
                    self.credential, "https://cognitiveservices.azure.com/.default"
                )
            elif settings.azure_openai_api_key:
                options["api_key"] = settings.azure_openai_api_key.get_secret_value()
            else:
                raise LLMError("Azure OpenAI API-key fallback requires an explicit key")
            self.client = AzureOpenAI(**options)
        except Exception:  # noqa: BLE001 - sanitized provider boundary
            raise LLMError("Azure OpenAI initialization failed") from None

    def name(self) -> str:
        return self.provider_name

    def as_runnable(self):
        from langchain_core.runnables import RunnableLambda

        async def invoke(request):
            return await self.generate_structured(
                request["stage"], request["context"], request["schema"]
            )

        return RunnableLambda(invoke, name="Azure structured reasoning")

    async def generate_structured(self, stage: str, context: dict, schema: type[T]) -> T:
        # One persistent paid-operation reservation is shared by concurrent
        # investigations. Queue locally rather than racing the ledger.
        async with self.request_lock:
            return await self._generate_structured(stage, context, schema)

    async def _generate_structured(self, stage: str, context: dict, schema: type[T]) -> T:
        output_bound = min(TOKEN_LIMITS.get(stage, 1200), self.settings.azure_max_output_tokens)
        packed, report = bounded_context(
            context,
            max(256, self.settings.azure_max_input_tokens - output_bound - 1536),
        )
        messages, response_format = structured_request(self.settings, stage, packed, schema)
        if report.compacted:
            await model_event(
                kind="context_compaction",
                status="completed",
                provider=self.provider_name,
                stage=stage,
                summary="Azure model context compacted while retaining source references",
                original_tokens=report.original_tokens,
                final_tokens=report.final_tokens,
            )
        attempts = 1 if stage == "readiness" else 2
        for attempt in range(attempts):
            delay = self.next_request_at - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self.next_request_at = (
                time.monotonic() + self.settings.azure_reasoning_min_interval_seconds
            )
            input_bound = estimate_request_tokens(
                {"messages": messages, "response_format": response_format}
            ) + 256

            def request(
                output_bound: int = output_bound,
                request_messages: list[dict[str, str]] = messages,
                request_format: dict[str, Any] = response_format,
            ) -> Any:
                return self.client.chat.completions.create(
                    model=self.deployment,
                    messages=request_messages,
                    response_format=request_format,
                    max_completion_tokens=output_bound,
                    timeout=self.settings.model_timeout_sec,
                    **({} if self.reasoning_model else {"temperature": 0}),
                )

            try:
                started = time.monotonic()
                response = await asyncio.wait_for(
                    asyncio.to_thread(request),
                    timeout=self.settings.model_timeout_sec,
                )
                content = response.choices[0].message.content
                if not isinstance(content, str):
                    raise ValueError("No structured content")
                counters = getattr(response, "usage", None)
                usage = {
                    "input_tokens": getattr(counters, "prompt_tokens", None),
                    "output_tokens": getattr(counters, "completion_tokens", None),
                    "total_tokens": getattr(counters, "total_tokens", None),
                }
                await model_event(
                    kind="llm_usage",
                    status="completed",
                    provider=self.provider_name,
                    stage=stage,
                    summary="Azure OpenAI request completed",
                    usage={key: value for key, value in usage.items() if isinstance(value, int)},
                )
            except Exception as exc:  # noqa: BLE001 - sanitized provider boundary
                code = azure_error_code(exc)
                category, action, recoverable, request_id = azure_failure(exc)
                await model_event(
                    kind="llm_error",
                    status="failed",
                    provider=self.provider_name,
                    stage=stage,
                    category=category,
                    request_id=request_id,
                    duration_ms=round((time.monotonic() - started) * 1000),
                    summary=f"Azure OpenAI {category.replace('_', ' ')}: {action}",
                )
                if category == "context_length" and attempt + 1 < attempts:
                    packed, report = bounded_context(packed, max(128, input_bound // 2))
                    messages, response_format = structured_request(
                        self.settings, stage, packed, schema
                    )
                    continue
                raise LLMError(
                    f"Azure OpenAI request failed [{category}]: {action} [{code}]",
                    category=category,
                    recoverable=recoverable,
                    request_id=request_id,
                ) from None
            try:
                return schema.model_validate_json(content)
            except (ValidationError, ValueError):
                if attempt + 1 < attempts:
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "The previous response failed schema validation. Return the"
                                " complete required JSON object, including every required key. No"
                                " prose."
                            ),
                        }
                    )
                    continue
                raise LLMError(
                    "Azure OpenAI returned invalid structured " + schema.__name__,
                    category="structured_output",
                    recoverable=True,
                ) from None
        raise LLMError("Azure OpenAI structured validation failed")

    def close(self) -> None:
        if hasattr(self.client, "close"):
            self.client.close()
        if self.credential and hasattr(self.credential, "close"):
            self.credential.close()
