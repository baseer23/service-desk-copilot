from backend.app.agent.models import Hypothesis, FinalResponse, EvidenceItem
from typing import Any, Literal
from pydantic import BaseModel, Field, computed_field
from backend.app.agent.models import MemoryFact


class IngestPasteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1_000_000)
    title: str | None = None


class IngestPasteResponse(BaseModel):
    chunks: int = Field(ge=0)
    entities: int = Field(ge=0)
    vector_count: int = Field(ge=0)
    ms: int = Field(ge=0)
    duplicate: bool = False
    content_hash: str | None = None

    @computed_field
    def chunks_ingested(self) -> int:
        return self.chunks

    @computed_field
    def entities_linked(self) -> int:
        return self.entities

    @computed_field
    def vectors_upserted(self) -> int:
        return self.vector_count

    @computed_field
    def latency_ms(self) -> int:
        return self.ms


class IngestPdfResponse(IngestPasteResponse):
    pages: int = Field(ge=0)

    @computed_field
    def pages_ingested(self) -> int:
        return self.pages


class IngestUrlRequest(BaseModel):
    url: str
    max_depth: int | None = Field(default=None, ge=0, le=3)
    max_pages: int | None = Field(default=None, ge=1, le=10)


class IngestUrlResponse(IngestPasteResponse):
    pages: int
    skipped_urls: list[str] = Field(default_factory=list)


class CaseCreateRequest(BaseModel):
    summary: str = Field(min_length=1, max_length=20000)


class CaseResumeRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)


class LegacyEvidence(BaseModel):
    tool: str
    source: str
    source_system: str
    observed_at: str
    content: Any


class RequestResult(BaseModel):
    ticket: dict[str, Any]
    status: Literal[
        "new",
        "investigating",
        "needs_input",
        "assessment_ready",
        "insufficient_information",
        "error",
    ]
    workflow: str | None = None
    verification: dict[str, Any] = Field(default_factory=dict)
    contradictions: list[str] = Field(default_factory=list)
    final_response: FinalResponse | None = None
    analysis: dict[str, Any] = Field(default_factory=dict)
    pending_question: str | None = None
    likely_issue: str | None = None
    hypotheses: list[Hypothesis | str] = Field(default_factory=list)
    confirmed_facts: list[MemoryFact] = Field(default_factory=list)
    remembered_facts: list[dict[str, Any]] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    recommended_next_action: str | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    uncertainty: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem | LegacyEvidence] = Field(default_factory=list)
    final_answer: str | None = None
    chat_messages: list[dict[str, str]] = Field(default_factory=list)
    research_iteration: int = 0


class CaseResponse(RequestResult):
    case_id: str
    thread_id: str
    origin: Literal["user", "demo", "test", "unknown"] = "unknown"
    resolution_status: Literal[
        "unconfirmed", "user_confirmed_resolved", "escalation_required"
    ] = "unconfirmed"


class CaseEvent(BaseModel):
    id: int
    tool: str | None
    source: str
    content: str
    created_at: str


class AskResponse(BaseModel):
    request_id: str
    intent: Literal["knowledge_question", "service_desk"]
    case_id: str | None
    result: RequestResult
    events: list[CaseEvent]


class HealthResponse(BaseModel):
    status: str
    generation_provider: str
    test_mode: bool
    mcp_connected: bool
    chroma: bool
    aura_configured: bool
    aura_connected: bool
    voice_enabled: bool = False
    voice_provider: str = "azure_voicelive"
    voicelive_configured: bool = False
    embedding_provider: str = "azure_openai"
    azure_openai_configured: bool = False
    readiness: dict[str, Any] = Field(default_factory=dict)
