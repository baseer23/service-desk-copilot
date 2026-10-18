"""Local persistence, selectable reasoning and recognition-only Speech settings."""

import json
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "DeskMate"
    allowed_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    generation_provider: Literal["groq", "azure_openai", "stub"] = "groq"
    embed_provider: Literal["sentence_transformers", "stub"] = "sentence_transformers"
    test_mode: bool = False
    deployment_mode: Literal["local", "production"] = "local"
    data_platform: Literal["local"] = "local"
    local_persistence_enabled: bool = False
    local_documents_dir: Path = PROJECT_ROOT / "store/local/documents"
    azure_document_endpoint: str = ""
    azure_document_api_version: str = "2024-11-30"
    azure_document_free_tier: bool = True
    azure_key_vault_url: str = ""
    azure_session_secret_name: str = "deskmate-session-signing-key"
    auth_client_id: str = ""
    auth_allowed_tenants: Annotated[list[str], NoDecode] = []
    auth_operator_ids: Annotated[list[str], NoDecode] = []
    enable_local_fixtures: bool = False
    enable_public_web_tools: bool = False
    groq_api_key: SecretStr | None = Field(default=None, repr=False)
    groq_model_name: str = "openai/gpt-oss-120b"
    groq_context_window_tokens: int = Field(default=0, ge=0, le=1_000_000)
    groq_max_output_tokens: int = Field(default=1500, ge=80, le=8192)
    groq_input_cad_per_million: float | None = Field(default=None, ge=0)
    groq_output_cad_per_million: float | None = Field(default=None, ge=0)
    groq_max_retries: int = Field(default=2, ge=0, le=4)
    groq_queue_timeout_sec: float = Field(default=120, ge=1, le=600)
    groq_max_concurrent_requests: int = Field(default=1, ge=1, le=4)
    groq_requests_per_minute: int = Field(default=0, ge=0)
    groq_requests_per_day: int = Field(default=0, ge=0)
    groq_tokens_per_minute: int = Field(default=0, ge=0)
    groq_tokens_per_day: int = Field(default=0, ge=0)
    provider_fallback_enabled: bool = False
    mcp_servers: Annotated[list[dict[str, Any]], NoDecode] = []
    speech_enabled: bool = False
    azure_speech_endpoint: str = ""
    azure_speech_language: str = "en-US"
    speech_max_concurrent_sessions: int = Field(default=1, ge=1, le=1)
    model_timeout_sec: int = Field(default=30, ge=1, le=120)
    azure_foundry_endpoint: str = Field(
        default="",
        description=(
            "Azure OpenAI resource origin, normally https://RESOURCE.openai.azure.com; "
            "not a project or Voice Live endpoint"
        ),
    )
    azure_model_deployment: str = ""
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_api_key: SecretStr | None = Field(default=None, repr=False)
    azure_use_entra_id: bool = True
    azure_structured_outputs: bool = True
    azure_reasoning_min_interval_seconds: float = Field(default=0, ge=0, le=120)
    azure_max_input_tokens: int = Field(default=32768, ge=256, le=32768)
    azure_max_output_tokens: int = Field(default=1500, ge=80, le=1500)
    embed_model_name: str = "all-MiniLM-L6-v2"
    voice_tenant_id: str = Field(default="local-demo", min_length=1, max_length=100)
    voice_session_signing_key: SecretStr | None = Field(default=None, repr=False)
    voice_auth_mode: Literal["local_demo", "entra"] = "local_demo"
    voice_auth_tenant_id: str = ""
    voice_auth_audience: str = ""
    neo4j_uri: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = Field(default=SecretStr(""), repr=False)
    neo4j_database: str = "neo4j"
    chroma_dir: Path = PROJECT_ROOT / "store/chroma"
    case_db_path: Path = PROJECT_ROOT / "store/case_memory.db"
    top_k: int = Field(default=6, ge=1, le=30)
    chunk_tokens: int = Field(default=512, ge=1)
    chunk_overlap: int = Field(default=64, ge=0)
    knowledge_scope: Literal["support", "broad"] = "support"
    research_source_policy: Literal["prefer_primary", "primary_only", "allow_third_party"] = (
        "prefer_primary"
    )
    research_preferred_domains: Annotated[list[str], NoDecode] = [
        "learn.microsoft.com",
        "support.microsoft.com",
        "docs.aws.amazon.com",
        "support.google.com",
        "support.apple.com",
        "developer.mozilla.org",
        "cisco.com",
        "fortinet.com",
        "watchguard.com",
        "okta.com",
        "canada.ca",
        "gov.uk",
        "nist.gov",
        "cisa.gov",
    ]
    max_research_rounds: int = Field(default=2, ge=0, le=3)
    max_investigation_cycles: int = Field(default=8, ge=1, le=20)
    allow_url_ingest: bool = True
    url_max_depth: int = Field(default=1, ge=0, le=3)
    url_max_pages: int = Field(default=5, ge=1, le=10)
    url_max_total_chars: int = Field(default=20000, ge=1000)
    url_rate_limit_sec: float = Field(default=1, ge=0)
    web_max_bytes: int = Field(default=2_000_000, ge=1024)
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", extra="ignore", hide_input_in_errors=True
    )

    @property
    def uses_record_store(self) -> bool:
        return self.local_persistence_enabled

    @field_validator(
        "allowed_origins",
        "research_preferred_domains",
        "auth_allowed_tenants",
        "auth_operator_ids",
        "mcp_servers",
        mode="before",
    )
    @classmethod
    def parse_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return (
                json.loads(value)
                if value.startswith("[")
                else [s.strip() for s in value.split(",") if s.strip()]
            )
        return value

    @field_validator(
        "voice_session_signing_key",
        "azure_openai_api_key",
        "groq_api_key",
        mode="before",
    )
    @classmethod
    def empty_secret(cls, value: str | SecretStr | None) -> str | SecretStr | None:
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_runtime(self) -> "Settings":
        if len(self.auth_allowed_tenants) > 8:
            raise ValueError("The pilot supports at most eight authorized tenants")
        if self.deployment_mode == "production":
            if not self.local_persistence_enabled:
                raise ValueError("Production requires tenant-scoped persistent local records")
            if (
                not self.auth_client_id
                or not self.auth_allowed_tenants
                or not self.voice_session_signing_key
            ):
                raise ValueError(
                    "Production requires an application audience, authorized tenants and a persistent signing key"
                )
            for identifier in [
                self.auth_client_id,
                *self.auth_allowed_tenants,
                *self.auth_operator_ids,
            ]:
                try:
                    uuid.UUID(identifier)
                except ValueError:
                    raise ValueError(
                        "Identity configuration requires valid object identifiers"
                    ) from None
            if self.enable_local_fixtures:
                raise ValueError("Production must disable fictional enterprise context")
        managed_endpoints = {
            "azure_document_endpoint": ".cognitiveservices.azure.com",
            "azure_key_vault_url": ".vault.azure.net",
        }
        for name, suffix in managed_endpoints.items():
            value = getattr(self, name)
            if value:
                parsed = urlsplit(value)
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or not parsed.hostname.endswith(suffix)
                    or parsed.username
                    or parsed.password
                    or parsed.query
                    or parsed.fragment
                    or parsed.path not in {"", "/"}
                    or parsed.port not in {None, 443}
                ):
                    raise ValueError("Managed endpoints require Microsoft HTTPS resource origins")
        if not self.test_mode and (
            self.generation_provider == "stub"
            or self.embed_provider == "stub"
        ):
            raise ValueError("Deterministic providers require TEST_MODE=true and isolated stores")
        if self.deployment_mode == "production" and (
            self.test_mode or not self.azure_use_entra_id or self.voice_auth_mode != "entra"
        ):
            raise ValueError(
                "Production requires Azure Entra identity and authenticated voice sessions"
            )
        if (
            self.voice_session_signing_key
            and len(self.voice_session_signing_key.get_secret_value()) < 32
        ):
            raise ValueError("VOICE_SESSION_SIGNING_KEY requires at least 32 characters")
        if (
            self.voice_session_signing_key
            and self.voice_session_signing_key.get_secret_value().startswith("@Microsoft.KeyVault(")
        ):
            raise ValueError("The persistent signing secret reference must resolve before startup")
        if self.neo4j_uri and not self.neo4j_uri.startswith(("neo4j+s://", "neo4j+ssc://")):
            raise ValueError("NEO4J_URI must be an Aura TLS URI or empty")
        if self.azure_speech_endpoint:
            parsed_speech = urlsplit(self.azure_speech_endpoint)
            if (
                parsed_speech.scheme != "https"
                or not parsed_speech.hostname
                or not parsed_speech.hostname.endswith(".cognitiveservices.azure.com")
                or parsed_speech.port not in {None, 443}
                or parsed_speech.username
                or parsed_speech.password
                or parsed_speech.query
                or parsed_speech.fragment
                or parsed_speech.path not in {"", "/"}
            ):
                raise ValueError("AZURE_SPEECH_ENDPOINT must be a Microsoft HTTPS custom-domain resource origin without credentials")
        for endpoint in (self.azure_foundry_endpoint,):
            if endpoint:
                parsed = urlsplit(endpoint)
                if (
                    parsed.scheme != "https"
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                    or parsed.query
                    or parsed.fragment
                    or parsed.path not in {"", "/"}
                    or not parsed.hostname.endswith(
                        (
                            ".openai.azure.com",
                            ".services.ai.azure.com",
                            ".cognitiveservices.azure.com",
                        )
                    )
                ):
                    raise ValueError(
                        "Azure endpoints must be Microsoft HTTPS resource origins without"
                        " credentials"
                    )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> None:
    get_settings.cache_clear()
