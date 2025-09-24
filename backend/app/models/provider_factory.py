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


SMALL_OLLAMA_MODELS: List[tuple[str, str]] = [
    ("phi3:mini", "Phi 3 Mini chosen for Mac Air responsiveness."),
    ("tinyllama", "TinyLlama fallback when thermals climb."),
]


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
        context = _auto_local(settings)
        if context is not None:
            return context
        logger.warning("No preferred local model detected; falling back to stub provider.")
        return _stub_context("No small local model detected; using deterministic stub.")

    if key == "ollama":
        model_name = settings.model_name or SMALL_OLLAMA_MODELS[0][0]
        return ProviderContext(
            provider=OllamaProvider(
                model_name=model_name,
                host=settings.ollama_host,
                timeout_sec=settings.model_timeout_sec,
            ),
            provider_type="local",
            model_name=model_name,
            reason=f"Ollama provider manually pinned to {model_name}.",
            local_model_available=True,
        )

    if key == "llamacpp":
        model_name = settings.model_name or ""
        return ProviderContext(
            provider=LlamaCppProvider(
                host=settings.llamacpp_host,
                model=model_name or None,
                timeout_sec=settings.model_timeout_sec,
            ),
            provider_type="local",
            model_name=model_name or "llama.cpp-default",
            reason="llama.cpp endpoint configured via settings.",
            local_model_available=True,
        )

    if key in {"hosted", "groq"}:
        return _groq_context(settings)

    if key == "stub":
        return _stub_context("Stub provider active (MODEL_PROVIDER=stub).")

    logger.warning("Unknown provider '%s'; falling back to stub provider.", key)
    return _stub_context(f"Unknown provider '{key}'; using deterministic stub.")


def _auto_local(settings: Settings) -> ProviderContext | None:
    models = _list_ollama_models(settings.ollama_host)
    if not models:
        return None
    for model_name, reason in SMALL_OLLAMA_MODELS:
        if any(entry.lower() == model_name for entry in models):
            context = ProviderContext(
                provider=OllamaProvider(
                    model_name=model_name,
                    host=settings.ollama_host,
                    timeout_sec=settings.model_timeout_sec,
                ),
                provider_type="local",
                model_name=model_name,
                reason=reason,
                local_model_available=True,
            )
            logger.info("Selected local model %s (%s)", model_name, reason)
            return context
    return None


def _groq_context(settings: Settings) -> ProviderContext:
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
        reason="Hosted Groq Llama 3.1 8B Instant ready for longer answers.",
        local_model_available=False,
    )


def _stub_context(reason: str) -> ProviderContext:
    return ProviderContext(
        provider=StubProvider(),
        provider_type="stub",
        model_name="stub",
        reason=reason,
        local_model_available=False,
    )


def _list_ollama_models(host: str) -> List[str]:
    url = f"{host.rstrip('/')}/api/tags"
    try:
        response = requests.get(url, timeout=1)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):  # pragma: no cover - network dependent
        return []
    models = data.get("models")
    names: List[str] = []
    if isinstance(models, list):
        for entry in models:
            if isinstance(entry, dict):
                name = entry.get("name")
                if isinstance(name, str):
                    names.append(name)
    return names
