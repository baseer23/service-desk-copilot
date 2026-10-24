"""Request-scoped model selection; credentials never cross the HTTP boundary."""

import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from backend.app.core.config import Settings

from .provider import LLMError

_selection: ContextVar[str | None] = ContextVar("deskmate_model_provider", default=None)
_route: ContextVar[dict[str, Any] | None] = ContextVar("deskmate_model_route", default=None)
_events: ContextVar[Callable[[dict], Any] | None] = ContextVar(
    "deskmate_model_events", default=None
)


def selected_provider(settings: Settings) -> str:
    route = _route.get()
    return (route or {}).get("active") or _selection.get() or settings.generation_provider


@contextmanager
def provider_scope(
    selection: str | None, event_sink: Callable[[dict], Any] | None = None
) -> Iterator[None]:
    if selection is not None and selection not in {
        "groq", "azure_openai", "azure_gpt5_nano", "stub"
    }:
        raise LLMError("Choose Groq, Azure OpenAI, or Azure OpenAI GPT-5 Nano")
    token = _selection.set(selection)
    route_token = _route.set(
        {"selected": selection, "active": selection, "fallback_attempted": False}
    )
    event_token = _events.set(event_sink)
    try:
        yield
    finally:
        _route.reset(route_token)
        _events.reset(event_token)
        _selection.reset(token)


async def model_event(**event: Any) -> None:
    sink = _events.get()
    if sink is not None:
        value = sink(event)
        if inspect.isawaitable(value):
            await value


class SelectableReasoningProvider:
    """One shared structured/LangChain interface for every graph stage.

    The selected implementation is captured at invocation; a separate request's
    selection cannot mutate a running graph. Missing credentials fail explicitly.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._providers: dict[str, Any] = {}

    def name(self) -> str:
        return selected_provider(self.settings)

    def configuration(self) -> dict[str, Any]:
        provider = self.name()
        missing: list[str] = []
        if provider == "groq":
            if not self.settings.groq_api_key:
                missing.append("GROQ_API_KEY")
            if not self.settings.groq_model_name:
                missing.append("GROQ_MODEL_NAME")
        elif provider in {"azure_openai", "azure_gpt5_nano"}:
            if not self.settings.azure_foundry_endpoint:
                missing.append("AZURE_FOUNDRY_ENDPOINT")
            deployment = (
                self.settings.azure_gpt5_nano_deployment
                if provider == "azure_gpt5_nano"
                else self.settings.azure_model_deployment
            )
            if not deployment:
                missing.append(
                    "AZURE_GPT5_NANO_DEPLOYMENT"
                    if provider == "azure_gpt5_nano"
                    else "AZURE_MODEL_DEPLOYMENT"
                )
            if not self.settings.azure_use_entra_id and not self.settings.azure_openai_api_key:
                missing.append("AZURE_OPENAI_API_KEY")
        elif not self.settings.test_mode:
            missing.append("TEST_MODE")
        return {"provider": provider, "configured": not missing, "missing": missing}

    def _current(self):
        name = self.name()
        if name not in self._providers:
            from .provider_factory import get_provider

            self._providers[name] = get_provider(
                self.settings.model_copy(update={"generation_provider": name})
            )
        return self._providers[name]

    def _provider(self, name: str):
        if name not in self._providers:
            from .provider_factory import get_provider

            self._providers[name] = get_provider(
                self.settings.model_copy(update={"generation_provider": name})
            )
        return self._providers[name]

    async def generate_structured(self, stage, context, schema):
        return await self.as_runnable().ainvoke(
            {"stage": stage, "context": context, "schema": schema}
        )

    def as_runnable(self):
        from langchain_core.runnables import RunnableLambda

        async def invoke(request):
            route = _route.get()
            active = selected_provider(self.settings)
            try:
                return await self._provider(active).generate_structured(
                    request["stage"], request["context"], request["schema"]
                )
            except LLMError as error:
                azure_ready = bool(
                    self.settings.azure_foundry_endpoint and self.settings.azure_model_deployment
                )
                can_fallback = bool(
                    self.settings.provider_fallback_enabled
                    and active == "groq"
                    and error.recoverable
                    and route is not None
                    and not route["fallback_attempted"]
                    and azure_ready
                )
                if not can_fallback:
                    raise
                route["fallback_attempted"] = True
                route["active"] = "azure_openai"
                await model_event(
                    kind="provider_fallback",
                    status="running",
                    provider="azure_openai",
                    previous_provider="groq",
                    stage=request["stage"],
                    category=error.category,
                    summary="Groq recovery was exhausted; trying the configured Azure fallback",
                )
                return await self._provider("azure_openai").generate_structured(
                    request["stage"], request["context"], request["schema"]
                )

        return RunnableLambda(invoke, name="DeskMate structured reasoning")

    async def aclose(self) -> None:
        for provider in self._providers.values():
            close = getattr(provider, "aclose", None)
            if close:
                await close()
            elif getattr(provider, "close", None):
                import asyncio

                await asyncio.to_thread(provider.close)
