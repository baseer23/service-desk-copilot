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


class AskResponse(BaseModel):
    answer: str
    provider: str
    question: str
    citations: List[Citation]
    planner: Dict[str, object]
    latency_ms: int = Field(..., ge=0)
    confidence: float = Field(..., ge=0.0, le=1.0)
