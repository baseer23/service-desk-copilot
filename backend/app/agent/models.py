from __future__ import annotations

from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field


class TicketAnalysis(BaseModel):
    """Structured output for ticket understanding."""

    user_name: str | None = Field(default=None)
    company: str | None = Field(default=None)
    reported_site: str | None = Field(default=None)
    device: str | None = Field(default=None)
    product: str | None = Field(default=None)
    category: str | None = Field(default=None)
    symptoms: str | None = Field(default=None)
    error_messages: List[str] = Field(default_factory=list)
    affected_scope: str | None = Field(default=None)
    important_entities: List[str] = Field(default_factory=list)
    initial_hypotheses: List[str] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)


class InvestigationDecision(BaseModel):
    """Structured output for the next investigation step."""

    hypotheses: List[str] = Field(default_factory=list)
    evidence_summary: str = Field(default="")
    missing_information: List[str] = Field(default_factory=list)
    next_tools: List[str] = Field(default_factory=list)
    question_for_human: str | None = Field(default=None)
    enough_information: bool = False
    confidence: float = Field(default=0.0)
    reasoning_summary: str = Field(default="")
    likely_issue: str | None = Field(default=None)
    recommended_next_action: str | None = Field(default=None)
    uncertainty: List[str] = Field(default_factory=list)
    status: Literal["continue", "needs_human", "web_research", "finalize"] = "continue"


class MemoryFact(BaseModel):
    """Persistent fact with provenance metadata."""

    subject: str
    predicate: str
    value: str
    source: str
    case_id: str | None = None
    observed_at: str
    confidence: float = 0.0
    source_system: str = "local_fixture"
    reliability: str = "confirmed"
    metadata: Dict[str, Any] = Field(default_factory=dict)
