"""Azure is the only live reasoning provider; fixtures require explicit test mode."""

from backend.app.core.config import Settings

from .provider import CaseReasoningProvider
from .provider_azure_openai import AzureOpenAIProvider


def get_provider(settings: Settings) -> CaseReasoningProvider:
    if settings.generation_provider == "stub" and settings.test_mode:
        from backend.app.testing.provider_stub import StubProvider

        return StubProvider()
    if settings.generation_provider != "azure_openai":
        raise ValueError("Only Azure reasoning is supported outside isolated tests")
    return AzureOpenAIProvider(settings)
