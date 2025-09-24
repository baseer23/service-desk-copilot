from __future__ import annotations

import os
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, computed_field, field_validator


class IngestPasteRequest(BaseModel):
    text: str = Field(..., min_length=1)
    title: Optional[str] = Field(default=None)


class IngestPasteResponse(BaseModel):
    chunks: int = Field(..., ge=0)
    entities: int = Field(..., ge=0)
    vector_count: int = Field(..., ge=0)
    ms: int = Field(..., ge=0)

    @computed_field  # type: ignore[misc]
    def chunks_ingested(self) -> int:
        return self.chunks

    @computed_field  # type: ignore[misc]
    def entities_linked(self) -> int:
        return self.entities

    @computed_field  # type: ignore[misc]
    def vectors_upserted(self) -> int:
        return self.vector_count

    @computed_field  # type: ignore[misc]
    def latency_ms(self) -> int:
        return self.ms


class IngestPdfResponse(BaseModel):
    pages: int = Field(..., ge=0)
    chunks: int = Field(..., ge=0)
    entities: int = Field(..., ge=0)
    vector_count: int = Field(..., ge=0)
    ms: int = Field(..., ge=0)

    @computed_field  # type: ignore[misc]
    def pages_ingested(self) -> int:
        return self.pages

    @computed_field  # type: ignore[misc]
    def chunks_ingested(self) -> int:
        return self.chunks

    @computed_field  # type: ignore[misc]
    def entities_linked(self) -> int:
        return self.entities

    @computed_field  # type: ignore[misc]
    def vectors_upserted(self) -> int:
        return self.vector_count

    @computed_field  # type: ignore[misc]
    def latency_ms(self) -> int:
        return self.ms


class Citation(BaseModel):
    doc_id: str
    chunk_id: str
    score: float = Field(..., ge=0.0)
    title: Optional[str] = None
    snippet: Optional[str] = None


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    top_k: Optional[int] = Field(default=None, ge=1)
    provider_override: Optional[str] = Field(default=None)

    @field_validator("top_k", mode="before")
    @classmethod
    def default_top_k(cls, value: Optional[int]) -> Optional[int]:
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
        if value is None:
            return None
        normalized = value.strip().lower()
        allowed = {"ollama", "groq", "stub", "llamacpp"}
        if normalized not in allowed:
            raise ValueError("provider_override must be one of ollama, groq, stub, or llamacpp")
        return normalized


class AskResponse(BaseModel):
    answer: str
    provider: str
    question: str
    citations: List[Citation]
    planner: Dict[str, object]
    latency_ms: int = Field(..., ge=0)
    confidence: float = Field(..., ge=0.0, le=1.0)


class ProviderToggleRequest(BaseModel):
    provider: str = Field(..., min_length=1)

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"ollama", "groq"}:
            raise ValueError("provider must be ollama or groq")
        return normalized


class IngestUrlRequest(BaseModel):
    url: str = Field(..., min_length=1)
    max_depth: Optional[int] = Field(default=None, ge=0)
    max_pages: Optional[int] = Field(default=None, ge=1)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("url must not be empty")
        if not value.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return value


class IngestUrlResponse(BaseModel):
    pages: int = Field(..., ge=0)
    chunks: int = Field(..., ge=0)
    entities: int = Field(..., ge=0)
    vector_count: int = Field(..., ge=0)
    ms: int = Field(..., ge=0)

    @computed_field  # type: ignore[misc]
    def pages_ingested(self) -> int:
        return self.pages

    @computed_field  # type: ignore[misc]
    def chunks_ingested(self) -> int:
        return self.chunks

    @computed_field  # type: ignore[misc]
    def entities_linked(self) -> int:
        return self.entities

    @computed_field  # type: ignore[misc]
    def vectors_upserted(self) -> int:
        return self.vector_count

    @computed_field  # type: ignore[misc]
    def latency_ms(self) -> int:
        return self.ms
