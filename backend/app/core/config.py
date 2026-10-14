"""Azure-only live settings. Private local environment files are never rewritten."""

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
    generation_provider: Literal["azure_openai", "stub"] = "azure_openai"
    embed_provider: Literal["azure_openai", "stub"] = "azure_openai"
    test_mode: bool = False
    deployment_mode: Literal["local", "production"] = "local"
    data_platform: Literal["local", "azure"] = "local"
    local_persistence_enabled: bool = False
    local_documents_dir: Path = PROJECT_ROOT / "store/local/documents"
    azure_cosmos_endpoint: str = ""
    azure_cosmos_database: str = "deskmate"
    azure_cosmos_container: str = "state"
    azure_storage_account_url: str = ""
    azure_storage_container: str = "documents"
    azure_search_endpoint: str = ""
    azure_search_index: str = "knowledge"
    azure_search_semantic_enabled: bool = True
    azure_document_endpoint: str = ""
    azure_document_api_version: str = "2024-11-30"
    azure_document_free_tier: bool = True
    azure_document_monthly_page_limit: int = Field(default=500, ge=1, le=500)
    azure_key_vault_url: str = ""
    azure_session_secret_name: str = "deskmate-session-signing-key"
    auth_client_id: str = ""
    auth_allowed_tenants: Annotated[list[str], NoDecode] = []
    auth_operator_ids: Annotated[list[str], NoDecode] = []
    enable_local_fixtures: bool = True
    enable_public_web_tools: bool = False
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
    # Independent local estimate guard, never an Azure invoice guarantee.
    azure_usage_guard_enabled: bool = False
    azure_emergency_disable: bool = False
    azure_setup_validation_mode: bool = False
    azure_estimated_setup_limit_cad: float = Field(default=2.0, gt=0, le=2)
    azure_setup_max_voice_seconds: int = Field(default=30, ge=1, le=60)
    azure_setup_validation_id: str = ""
    azure_reasoning_min_interval_seconds: float = Field(default=0, ge=0, le=120)
    azure_embedding_min_interval_seconds: float = Field(default=0, ge=0, le=60)
    azure_live_test_mode: bool = False
    azure_estimated_monthly_app_limit_cad: float = Field(default=8.0, gt=0, le=250)
    azure_estimated_infrastructure_cad: float = Field(default=0.25, ge=0, le=200)
    azure_usage_db_path: Path | None = None
    azure_max_concurrent_sessions: int = Field(default=1, ge=1, le=1)
    azure_max_voice_session_minutes: int = Field(default=2, ge=1, le=10)
    azure_max_turns_per_session: int = Field(default=20, ge=1, le=20)
    azure_max_live_sessions_per_day: int = Field(default=2, ge=0, le=20)
    azure_max_input_tokens: int = Field(default=32768, ge=256, le=32768)
    azure_max_output_tokens: int = Field(default=1500, ge=80, le=1500)
    # Deliberately conservative assumptions in CAD, not published Azure prices.
    azure_estimated_input_cad_per_million: float = Field(default=10, ge=10, allow_inf_nan=False)
    azure_estimated_output_cad_per_million: float = Field(default=40, ge=40, allow_inf_nan=False)
    azure_estimated_embedding_cad_per_million: float = Field(default=1, ge=1, allow_inf_nan=False)
    azure_estimated_voice_cad_per_minute: float = Field(default=0.5, ge=0.5, allow_inf_nan=False)
    azure_embedding_deployment: str = ""
    embed_model_name: str = "text-embedding-3-small"
    azure_embedding_dimensions: int = Field(default=384, ge=256, le=3072)
    azure_voicelive_endpoint: str = ""
    azure_voicelive_model: str = "gpt-realtime"
    azure_voicelive_api_version: str = "2026-04-10"
    azure_voicelive_api_key: SecretStr | None = Field(default=None, repr=False)
    azure_voicelive_voice: str = "en-US-AvaNeural"
    voice_enabled: bool = False
    voice_provider: Literal["azure_voicelive", "fake"] = "azure_voicelive"
    voice_tenant_id: str = Field(default="local-demo", min_length=1, max_length=100)
    voice_session_signing_key: SecretStr | None = Field(default=None, repr=False)
    voice_auth_mode: Literal["local_demo", "entra"] = "local_demo"
    voice_auth_tenant_id: str = ""
    voice_auth_audience: str = ""
    voice_session_timeout_sec: int = Field(default=900, ge=30, le=3600)
    voice_max_turns: int = Field(default=1000, ge=20, le=2000)
    voice_max_confirmation_retries: int = Field(default=3, ge=1, le=5)
    voice_transcript_retention_hours: int = Field(default=24, ge=0, le=168)
    neo4j_uri: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = Field(default=SecretStr(""), repr=False)
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
        return self.data_platform == "azure" or self.local_persistence_enabled

    @field_validator(
        "allowed_origins",
        "research_preferred_domains",
        "auth_allowed_tenants",
        "auth_operator_ids",
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
        "azure_voicelive_api_key",
        mode="before",
    )
    @classmethod
    def empty_secret(cls, value: str | SecretStr | None) -> str | SecretStr | None:
        return None if value == "" else value

    @field_validator("azure_usage_db_path", mode="before")
    @classmethod
    def empty_usage_path(cls, value: Any) -> Any:
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_runtime(self) -> "Settings":
        if len(self.auth_allowed_tenants) > 8:
            raise ValueError("The pilot supports at most eight authorized tenants")
        if self.uses_record_store and not self.azure_document_free_tier:
            raise ValueError("Paid document analysis requires an explicitly approved cost policy")
        if self.deployment_mode == "production":
            if self.data_platform != "azure":
                raise ValueError("Production tenant isolation requires managed Azure storage")
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
            "azure_cosmos_endpoint": ".documents.azure.com",
            "azure_storage_account_url": ".blob.core.windows.net",
            "azure_search_endpoint": ".search.windows.net",
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
            elif self.data_platform == "azure" and name != "azure_key_vault_url":
                raise ValueError("Managed storage and document endpoints are required")
        if self.azure_live_test_mode:
            self.allow_url_ingest = False
            self.max_research_rounds = 0
            self.voice_session_timeout_sec = min(
                self.voice_session_timeout_sec, self.azure_max_voice_session_minutes * 60
            )
        if not self.test_mode and (
            self.generation_provider == "stub"
            or self.embed_provider == "stub"
            or self.voice_provider == "fake"
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
        for endpoint in (self.azure_foundry_endpoint, self.azure_voicelive_endpoint):
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
