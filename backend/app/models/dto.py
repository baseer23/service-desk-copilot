from __future__ import annotations

import os
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, computed_field, field_validator


class IngestPasteRequest(BaseModel):
    """Payload accepted when ingesting pasted text."""
    text: str = Field(..., min_length=1)
    title: Optional[str] = Field(default=None)


class IngestPasteResponse(BaseModel):
    """Summary metrics returned after ingesting pasted text."""
    chunks: int = Field(..., ge=0)
    entities: int = Field(..., ge=0)
    vector_count: int = Field(..., ge=0)
    ms: int = Field(..., ge=0)

    @computed_field  # type: ignore[misc]
    def chunks_ingested(self) -> int:
        """Maintain compatibility with earlier response field names."""
        return self.chunks

    @computed_field  # type: ignore[misc]
    def entities_linked(self) -> int:
        """Expose entity counts under legacy naming."""
        return self.entities

    @computed_field  # type: ignore[misc]
    def vectors_upserted(self) -> int:
        """Alias vector counts for older clients."""
        return self.vector_count

    @computed_field  # type: ignore[misc]
    def latency_ms(self) -> int:
        """Expose total processing latency in milliseconds."""
        return self.ms


class IngestPdfResponse(BaseModel):
    """Response payload describing PDF ingestion results."""
    pages: int = Field(..., ge=0)
    chunks: int = Field(..., ge=0)
    entities: int = Field(..., ge=0)
    vector_count: int = Field(..., ge=0)
    ms: int = Field(..., ge=0)

    @computed_field  # type: ignore[misc]
    def pages_ingested(self) -> int:
        """Return the number of pages processed."""
        return self.pages

    @computed_field  # type: ignore[misc]
    def chunks_ingested(self) -> int:
        """Maintain compatibility with earlier response field names."""
        return self.chunks

    @computed_field  # type: ignore[misc]
    def entities_linked(self) -> int:
        """Expose entity counts under legacy naming."""
        return self.entities

    @computed_field  # type: ignore[misc]
    def vectors_upserted(self) -> int:
        """Alias vector counts for older clients."""
        return self.vector_count

    @computed_field  # type: ignore[misc]
    def latency_ms(self) -> int:
        """Expose total processing latency in milliseconds."""
        return self.ms


class Citation(BaseModel):
    """Citation metadata surfaced alongside answers."""
    doc_id: str
    chunk_id: str
    score: float = Field(..., ge=0.0)
    title: Optional[str] = None
    snippet: Optional[str] = None


class AskRequest(BaseModel):
    """Ask endpoint payload containing the user question."""
    question: str = Field(..., min_length=1)
    top_k: Optional[int] = Field(default=None, ge=1)
    provider_override: Optional[str] = Field(default=None)

    @field_validator("top_k", mode="before")
    @classmethod
    def default_top_k(cls, value: Optional[int]) -> Optional[int]:
        """Prefer explicit payload values, falling back to environment defaults."""
        if value is not None:
            return value
        env_value = os.getenv("TOP_K")
        if env_value:
            try:
                parsed = int(env_value)
                if parsed > 0:
                    return parsed
            except ValueError:
                return None
        return None

    @field_validator("provider_override")
    @classmethod
    def validate_override(cls, value: Optional[str]) -> Optional[str]:
        """Normalise and validate provider overrides from the client."""
        if value is None:
            return None
        normalized = value.strip().lower()
        allowed = {"ollama", "groq", "stub", "llamacpp"}
        if normalized not in allowed:
            raise ValueError("provider_override must be one of ollama, groq, stub, or llamacpp")
        return normalized


class AskResponse(BaseModel):
    """Response body returned by the ask endpoint."""
    answer: str
    provider: str
    question: str
    citations: List[Citation]
    planner: Dict[str, object]
    latency_ms: int = Field(..., ge=0)
    confidence: float = Field(..., ge=0.0, le=1.0)


class ProviderToggleRequest(BaseModel):
    """Admin payload used to switch the active provider."""
    provider: str = Field(..., min_length=1)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        """Ensure toggled providers are restricted to supported options."""
        normalized = value.strip().lower()
        if normalized not in {"ollama", "groq"}:
            raise ValueError("provider must be ollama or groq")
        return normalized


class IngestUrlRequest(BaseModel):
    """Payload describing a crawl request for URL ingestion."""
    url: str = Field(..., min_length=1)
    max_depth: Optional[int] = Field(default=None, ge=0)
    max_pages: Optional[int] = Field(default=None, ge=1)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """Validate URL ingestion targets and enforce HTTP(S) scheme."""
        value = value.strip()
        if not value:
            raise ValueError("url must not be empty")
        if not value.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return value


class IngestUrlResponse(BaseModel):
    """Metrics captured after ingesting content from URLs."""
    pages: int = Field(..., ge=0)
    chunks: int = Field(..., ge=0)
    entities: int = Field(..., ge=0)
    vector_count: int = Field(..., ge=0)
    ms: int = Field(..., ge=0)

    @computed_field  # type: ignore[misc]
    def pages_ingested(self) -> int:
        """Return the number of pages processed."""
        return self.pages

    @computed_field  # type: ignore[misc]
    def chunks_ingested(self) -> int:
        """Maintain compatibility with earlier response field names."""
        return self.chunks

    @computed_field  # type: ignore[misc]
    def entities_linked(self) -> int:
        """Expose entity counts under legacy naming."""
        return self.entities

    @computed_field  # type: ignore[misc]
    def vectors_upserted(self) -> int:
        """Alias vector counts for older clients."""
        return self.vector_count

    @computed_field  # type: ignore[misc]
    def latency_ms(self) -> int:
        """Expose total processing latency in milliseconds."""
        return self.ms
