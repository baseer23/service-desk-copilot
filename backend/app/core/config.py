from functools import lru_cache
from pathlib import Path
from typing import List, Union

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_STUB_ANSWER = "hi, this was a test you pass"
DEFAULT_ALLOWED_ORIGINS = ["http://localhost:5173"]
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Application configuration derived from environment variables."""

    app_name: str = "Service Desk Copilot"
    allowed_origins: Union[List[str], str] = Field(default_factory=lambda: DEFAULT_ALLOWED_ORIGINS.copy())
    model_provider: str = "stub"
    model_name: str = "llama3:8b"
    model_timeout_sec: int = 20
    log_dir: Path = Path("logs")

    # Graph / Vector
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "neo4j"
    chroma_dir: Path = Path("store/chroma")

    # Embeddings
    embed_provider: str = "auto"
    embed_model_name: str = "all-MiniLM-L6-v2"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_host: str = "http://localhost:11434"
    llamacpp_host: str = "http://localhost:8080"

    # Planner / RAG
    top_k: int = 6
    chunk_tokens: int = 512
    chunk_overlap: int = 64

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def split_origins(cls, value):  # noqa: D401
        """Support plain or comma-delimited strings in the env file."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return list(value)

    @field_validator("model_provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        return value.lower()

    @field_validator("embed_provider")
    @classmethod
    def normalize_embed_provider(cls, value: str) -> str:
        return (value or "stub").lower()

    @field_validator("top_k", "chunk_tokens")
    @classmethod
    def positive_int(cls, value: int) -> int:
        return max(1, int(value))

    @field_validator("chunk_overlap")
    @classmethod
    def non_negative(cls, value: int) -> int:
        return max(0, int(value))


@lru_cache
def get_settings() -> Settings:
    """Return cached settings (re-computed only when module reloaded)."""
    settings = Settings()
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    settings.chroma_dir.mkdir(parents=True, exist_ok=True)
    return settings


def reload_settings() -> None:
    """Clear cached settings – primarily for tests."""
    get_settings.cache_clear()  # type: ignore[attr-defined]
