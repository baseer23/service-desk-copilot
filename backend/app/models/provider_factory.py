from .provider_groq import GroqHostedProvider
from .provider_stub import StubProvider


def get_provider(settings):
    if settings.generation_provider == "stub" and settings.test_mode:
        return StubProvider()
    if settings.generation_provider != "groq":
        raise ValueError("Unsupported generation provider")
    return GroqHostedProvider(
        settings.groq_api_key,
        settings.hosted_model_name,
        settings.model_timeout_sec,
        settings.groq_api_url,
    )
