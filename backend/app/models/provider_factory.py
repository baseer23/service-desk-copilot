from .provider import LocalModelProvider
from ..core.config import get_settings
from .provider_stub import StubProvider
from .provider_ollama import OllamaProvider
from .provider_llamacpp import LlamaCppProvider


def get_provider() -> LocalModelProvider:
    s = get_settings()
    provider = s.model_provider
    if provider == "ollama":
        return OllamaProvider(model=s.model_name, timeout_sec=s.model_timeout_sec)
    if provider == "llamacpp":
        return LlamaCppProvider(timeout_sec=s.model_timeout_sec)
    # default stub
    return StubProvider()

