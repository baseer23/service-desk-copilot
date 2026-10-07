"""Factory helpers for selecting the active provider and its metadata."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List

import requests

from backend.app.core.config import Settings

from .provider import LocalModelProvider
from .provider_groq import GroqHostedProvider
from .provider_llamacpp import LlamaCppProvider
from .provider_ollama import OllamaProvider
from .provider_stub import StubProvider


logger = logging.getLogger(__name__)


@dataclass
class ProviderContext:
    """Active provider along with operator-facing context."""

    provider: LocalModelProvider
    provider_type: str
    model_name: str
    vendor: str | None = None
    reason: str | None = None
    local_model_available: bool = False


def get_provider(settings: Settings) -> LocalModelProvider:
    """Retained for backwards compatibility with existing imports."""

    return select_provider(settings).provider


def select_provider(settings: Settings) -> ProviderContext:
    """Return the most appropriate provider for the current environment."""

    provider_key = (settings.model_provider or "stub").lower()
    return build_provider_context(settings, provider_key)


def build_provider_context(settings: Settings, provider_key: str) -> ProviderContext:
    """Resolve a provider context for the given key with sensible fallbacks."""

    key = (provider_key or "stub").lower()

    if key in {"auto", "local"}:
        if getattr(settings, "groq_api_key", None):
            return _groq_context(settings)
        logger.warning("No hosted model configured; falling back to stub provider.")
        return _stub_context("No hosted model configured; using deterministic stub.")

    if key in {"ollama"}:
        return _ollama_context(settings)

    if key in {"llamacpp"}:
        return _llamacpp_context(settings)

    if key in {"hosted", "groq", "hosted-groq"}:
        return _groq_context(settings)

    if key == "stub":
        return _stub_context("Stub provider active (MODEL_PROVIDER=stub).")

    logger.warning("Unknown provider '%s'; falling back to stub provider.", key)
    return _stub_context(f"Unknown provider '{key}'; using deterministic stub.")


def _groq_context(settings: Settings) -> ProviderContext:
    """Build a provider context targeting the hosted Groq endpoint."""
    model_name = settings.hosted_model_name or settings.model_name
    try:
        provider = GroqHostedProvider(
            api_key=settings.groq_api_key,
            model_name=model_name,
            timeout_sec=settings.model_timeout_sec,
            api_url=settings.groq_api_url,
        )
    except Exception as exc:  # pragma: no cover - defensive fall back
        logger.warning("Hosted provider unavailable; using stub context (%s)", exc)
        return _stub_context(f"Hosted provider unavailable ({exc}); falling back to stub.")
    return ProviderContext(
        provider=provider,
        provider_type="hosted",
        model_name=model_name,
        vendor="Groq",
        reason="Hosted Groq inference ready for longer answers.",
        local_model_available=False,
    )


def _stub_context(reason: str) -> ProviderContext:
    """Return a stub provider context with the supplied reason."""
    return ProviderContext(
        provider=StubProvider(),
        provider_type="stub",
        model_name="stub",
        reason=reason,
        local_model_available=False,
    )


def _ollama_context(settings: Settings) -> ProviderContext:
    """Build a provider context for Ollama."""
    model_name = getattr(settings, "model_name", "phi3:mini") or "phi3:mini"
    provider = OllamaProvider(model_name=model_name, host=getattr(settings, "ollama_host", "http://localhost:11434"), timeout_sec=settings.model_timeout_sec)
    return ProviderContext(
        provider=provider,
        provider_type="ollama",
        model_name=model_name,
        vendor="Ollama",
        reason="Ollama local model ready.",
        local_model_available=True,
    )


def _llamacpp_context(settings: Settings) -> ProviderContext:
    """Build a provider context for llama.cpp."""
    model_name = getattr(settings, "model_name", "default") or "default"
    provider = LlamaCppProvider(model_name=model_name, host=getattr(settings, "llamacpp_host", "http://localhost:8080"), timeout_sec=settings.model_timeout_sec)
    return ProviderContext(
        provider=provider,
        provider_type="llamacpp",
        model_name=model_name,
        vendor="llama.cpp",
        reason="llama.cpp local model ready.",
        local_model_available=True,
    )
