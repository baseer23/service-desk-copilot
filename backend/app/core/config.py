from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
import json
from pydantic import Field, model_validator, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, NoDecode

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "DeskMate"
    allowed_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    generation_provider: Literal["groq", "stub"] = "groq"
    test_mode: bool = False
    model_timeout_sec: int = Field(default=30, ge=1)
    hosted_model_name: str = "openai/gpt-oss-20b"
    groq_api_key: str | None = Field(default=None, repr=False)
    groq_api_url: str = "https://api.groq.com/openai/v1/chat/completions"
    neo4j_uri: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: str = Field(default="", repr=False)
    chroma_dir: Path = PROJECT_ROOT / "store/chroma"
    case_db_path: Path = PROJECT_ROOT / "store/case_memory.db"
    embed_provider: Literal["sentence", "stub"] = "sentence"
    embed_model_name: Literal["all-MiniLM-L6-v2"] = "all-MiniLM-L6-v2"
    top_k: int = Field(default=6, ge=1, le=30)
    chunk_tokens: int = Field(default=512, ge=1)
    chunk_overlap: int = Field(default=64, ge=0)
    max_research_rounds: int = Field(default=2, ge=0, le=3)
    max_investigation_cycles: int = Field(default=8, ge=1, le=20)
    allow_url_ingest: bool = True
    url_max_depth: int = Field(default=1, ge=0, le=3)
    url_max_pages: int = Field(default=5, ge=1, le=10)
    url_max_total_chars: int = Field(default=20000, ge=1000)
    url_rate_limit_sec: float = Field(default=1, ge=0)
    web_max_bytes: int = Field(default=2_000_000, ge=1024)
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, value):
        if isinstance(value, str):
            return (
                json.loads(value)
                if value.startswith("[")
                else [s.strip() for s in value.split(",") if s.strip()]
            )
        return value

    @model_validator(mode="after")
    def test_providers_only(self):
        if not self.test_mode and (
            self.generation_provider == "stub" or self.embed_provider == "stub"
        ):
            raise ValueError("Stub generation and embeddings require TEST_MODE=true")
        if self.neo4j_uri and not self.neo4j_uri.startswith(("neo4j+s://", "neo4j+ssc://")):
            raise ValueError("NEO4J_URI must be an Aura TLS URI or empty")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> None:
    get_settings.cache_clear()
