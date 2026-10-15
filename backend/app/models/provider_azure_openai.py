"""Azure case reasoning, using Entra ID by default and bounded validation."""

import asyncio
import json
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from backend.app.core.azure_errors import azure_error_code
from backend.app.core.azure_usage import AzureUsageBlocked, AzureUsageGuard
from backend.app.core.config import Settings

from .instructions import stage_instructions
from .provider import LLMError
from .provider_routing import model_event

T = TypeVar("T", bound=BaseModel)
TOKEN_LIMITS = {
    "readiness": 80,
    "classify_intent": 220,
    "general_chat": 1000,
    "analyze_ticket": 600,
    "plan": 1400,
    "reason": 1500,
    "verify": 800,
    "evaluate_sources": 350,
}


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
    def __init__(self, settings: Settings, client: Any = None) -> None:
        if not settings.azure_foundry_endpoint or not settings.azure_model_deployment:
            raise LLMError("Azure OpenAI endpoint and deployment must be configured")
        self.settings = settings
        self.usage_guard = AzureUsageGuard(settings)
        self.request_lock = asyncio.Lock()
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
        return "azure_openai"

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
        messages, response_format = structured_request(self.settings, stage, context, schema)
        attempts = 1 if stage == "readiness" or self.settings.azure_setup_validation_mode else 2
        for attempt in range(attempts):
            try:
                while True:
                    delay = (
                        await asyncio.to_thread(self.usage_guard.request_delay, "reasoning")
                        if self.settings.uses_record_store
                        else self.usage_guard.request_delay("reasoning")
                    )
                    if not delay:
                        break
                    await asyncio.sleep(min(delay, 1))
            except AzureUsageBlocked as exc:
                raise LLMError(str(exc)) from None
            input_bound = (
                len(json.dumps(messages, ensure_ascii=False).encode())
                + len(json.dumps(response_format, ensure_ascii=False).encode())
                + 1024
            )
            output_bound = min(TOKEN_LIMITS.get(stage, 1200), self.settings.azure_max_output_tokens)

            def request(input_bound: int = input_bound, output_bound: int = output_bound) -> Any:
                reservation = self.usage_guard.reserve("reasoning", input_bound, output_bound)
                response = None
                try:
                    # Recheck the persistent emergency switch immediately before dispatch.
                    self.usage_guard.require_enabled()
                    response = self.client.chat.completions.create(
                        model=self.settings.azure_model_deployment,
                        messages=messages,
                        response_format=response_format,
                        max_completion_tokens=output_bound,
                        temperature=0,
                        timeout=self.settings.model_timeout_sec,
                    )
                    return response
                finally:
                    # Runs inside the SDK thread even if the awaiting task times out.
                    self.usage_guard.finish(reservation, getattr(response, "usage", None))

            try:
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
                    provider="azure_openai",
                    stage=stage,
                    summary="Azure OpenAI request completed",
                    usage={key: value for key, value in usage.items() if isinstance(value, int)},
                )
            except AzureUsageBlocked as exc:
                raise LLMError(str(exc)) from None
            except Exception as exc:  # noqa: BLE001 - sanitized provider boundary
                code = azure_error_code(exc)
                raise LLMError(
                    f"Azure OpenAI request failed or returned no message [{code}]"
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
                    "Azure OpenAI returned invalid structured " + schema.__name__
                ) from None
        raise LLMError("Azure OpenAI structured validation failed")

    def close(self) -> None:
        if hasattr(self.client, "close"):
            self.client.close()
        if self.credential and hasattr(self.credential, "close"):
            self.credential.close()
