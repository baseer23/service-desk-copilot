import pytest
from pydantic import ValidationError
from backend.app.core.config import Settings
from backend.app.models.provider_factory import get_provider
from backend.app.models.provider import LLMError


def test_health_returns_status_and_provider(make_client):
    client = make_client()
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["generation_provider"] == "stub"
    assert body["mcp_connected"] is True
    assert body["chroma"] is True


def test_stub_requires_explicit_test_mode():
    with pytest.raises(ValidationError, match="TEST_MODE"):
        Settings(_env_file=None, test_mode=False, generation_provider="stub")


def test_live_provider_does_not_fall_back():
    with pytest.raises(LLMError, match="endpoint and deployment"):
        get_provider(
            Settings(
                _env_file=None,
                generation_provider="azure_openai",
                azure_foundry_endpoint="",
                azure_model_deployment="",
                neo4j_uri="",
            )
        )
