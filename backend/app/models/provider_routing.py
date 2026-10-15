"""Request-scoped model selection; credentials never cross the HTTP boundary."""

import inspect
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from backend.app.core.config import Settings

from .provider import LLMError

_selection: ContextVar[str | None] = ContextVar("deskmate_model_provider", default=None)
_events: ContextVar[Callable[[dict], Any] | None] = ContextVar(
    "deskmate_model_events", default=None
)


def selected_provider(settings: Settings) -> str:
    return _selection.get() or settings.generation_provider


@contextmanager
def provider_scope(
    selection: str | None, event_sink: Callable[[dict], Any] | None = None
) -> Iterator[None]:
    if selection is not None and selection not in {"groq", "azure_openai", "stub"}:
        raise LLMError("Choose Groq or Azure OpenAI")
    token = _selection.set(selection)
    event_token = _events.set(event_sink)
    try:
        yield
    finally:
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
        elif provider == "azure_openai":
            if not self.settings.azure_foundry_endpoint:
                missing.append("AZURE_FOUNDRY_ENDPOINT")
            if not self.settings.azure_model_deployment:
                missing.append("AZURE_MODEL_DEPLOYMENT")
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

    async def generate_structured(self, stage, context, schema):
        return await self.as_runnable().ainvoke(
            {"stage": stage, "context": context, "schema": schema}
        )

    def as_runnable(self):
        from langchain_core.runnables import RunnableLambda

        async def invoke(request):
            return await self._current().generate_structured(
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
