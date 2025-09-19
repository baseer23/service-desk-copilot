import os
from dataclasses import dataclass
from typing import List


DEFAULT_STUB_ANSWER = "hi, this was a test you pass"


@dataclass
class Settings:
    """Application settings read from environment variables.

    Fresh instances should be created when you want to respect current env.
    """

    app_name: str = os.getenv("APP_NAME", "DeskMate — GraphRAG Service Desk Pilot")
    allowed_origins: List[str] = (
        os.getenv(
            "ALLOWED_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000",
        ).split(",")
    )

    model_provider: str = os.getenv("MODEL_PROVIDER", "stub").lower()
    model_name: str = os.getenv("MODEL_NAME", "tinyllama")
    model_timeout_sec: int = int(os.getenv("MODEL_TIMEOUT_SEC", "20"))


def get_settings() -> Settings:
    """Return a fresh Settings instance so env changes are picked up."""
    return Settings()

