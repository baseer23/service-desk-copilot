"""Azure-only case reasoning, using Entra ID by default and bounded validation."""

import asyncio
import json
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from backend.app.core.azure_errors import azure_error_code
from backend.app.core.azure_usage import AzureUsageBlocked, AzureUsageGuard
from backend.app.core.config import Settings

from .instructions import stage_instructions
from .provider import LLMError

T = TypeVar("T", bound=BaseModel)
TOKEN_LIMITS = {
    "readiness": 80,
    "classify_intent": 220,
    "analyze_ticket": 600,
    "plan": 1400,
    "reason": 1500,
    "verify": 800,
    "evaluate_sources": 350,
}


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
        return all(convert(item) for item in node.values())

    return schema if convert(schema) else None


class AzureOpenAIProvider:
    def __init__(self, settings: Settings, client: Any = None) -> None:
        if not settings.azure_foundry_endpoint or not settings.azure_model_deployment:
            raise LLMError("Azure OpenAI endpoint and deployment must be configured")
        self.settings = settings
        self.usage_guard = AzureUsageGuard(settings)
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

    async def generate_structured(self, stage: str, context: dict, schema: type[T]) -> T:
        instructions = stage_instructions(stage, context)
        raw_schema = schema.model_json_schema()
        supported = strict_schema(raw_schema) if self.settings.azure_structured_outputs else None
        response_format = (
            {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "strict": True, "schema": supported},
            }
            if supported
            else {"type": "json_object"}
        )
        messages = [
            {
                "role": "system",
                "content": instructions + "\nRequired JSON schema:\n" + json.dumps(raw_schema),
            },
            {"role": "user", "content": json.dumps(context, default=str)},
        ]
        attempts = 1 if stage == "readiness" or self.settings.azure_setup_validation_mode else 2
        for attempt in range(attempts):
            try:
                while delay := self.usage_guard.request_delay("reasoning"):
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
