"""Explicit provider construction. Never switch providers after a failed call."""

from backend.app.core.config import Settings

from .provider import CaseReasoningProvider
from .provider_azure_openai import AzureOpenAIProvider


def get_provider(settings: Settings) -> CaseReasoningProvider:
    if settings.generation_provider == "stub" and settings.test_mode:
        from backend.app.testing.provider_stub import StubProvider

        return StubProvider()
    if settings.generation_provider == "groq":
        from .provider_groq import GroqProvider

        return GroqProvider(settings)
    if settings.generation_provider == "azure_openai":
        return AzureOpenAIProvider(settings)
    raise ValueError("Only configured Groq or Azure OpenAI reasoning is supported")


def get_selectable_provider(settings: Settings) -> CaseReasoningProvider:
    from .provider_routing import SelectableReasoningProvider

    return SelectableReasoningProvider(settings)
