"""Factory helpers for selecting the active local provider."""

from __future__ import annotations

from backend.app.core.config import Settings

from .provider import LocalModelProvider
from .provider_llamacpp import LlamaCppProvider
from .provider_ollama import OllamaProvider
from .provider_stub import StubProvider


def get_provider(settings: Settings) -> LocalModelProvider:
    provider = (settings.model_provider or "stub").lower()
    if provider == "ollama":
        return OllamaProvider(
            model_name=settings.model_name,
            host=settings.ollama_host,
            timeout_sec=settings.model_timeout_sec,
        )
    if provider == "llamacpp":
        return LlamaCppProvider(
            host=settings.llamacpp_host,
            model=settings.model_name,
            timeout_sec=settings.model_timeout_sec,
        )
    return StubProvider()
