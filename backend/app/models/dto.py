from backend.app.agent.models import Hypothesis, FinalResponse, EvidenceItem
from typing import Any, Literal
from pydantic import BaseModel, Field, computed_field, model_validator
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
    max_depth: int | None = Field(default=None, ge=0, le=10)
    max_pages: int | None = Field(default=None, ge=1, le=2000)


class IngestUrlResponse(IngestPasteResponse):
    pages: int
    skipped_urls: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    local_index_ready: bool = False
    graph_link_status: Literal["linked", "pending", "not_configured"] = "pending"
    semantic_graph_status: str = "pending"
    status: Literal["ready", "graph_pending", "partial"] = "ready"
    failed_pages: int = Field(default=0, ge=0)


class CaseCreateRequest(BaseModel):
    summary: str = Field(min_length=1, max_length=20000)


class CaseResumeRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)


class MemorySaveRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=240)


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
    explicit_saved_knowledge: bool = False
    # available | empty | stale | unavailable | error; None when no graph read ran.
    graph_status: str | None = None
    executed_tool_calls: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_empty_legacy_final_response(cls, value):
        """Treat old empty response placeholders as absent without rewriting history."""
        if isinstance(value, dict) and value.get("final_response") == {}:
            value = {**value, "final_response": None}
        return value


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
    activity: list[dict[str, Any]] = Field(default_factory=list)


class EmailTurnRequest(BaseModel):
    summary: str = Field(min_length=1, max_length=4000)
    request_id: str | None = Field(default=None, min_length=8, max_length=100)
    expected_version: int | None = Field(default=None, ge=0)


class HealthResponse(BaseModel):
    status: str
    generation_provider: str
    test_mode: bool
    mcp_connected: bool
    mcp_integrations: dict[str, str] = Field(default_factory=dict)
    chroma: bool
    aura_configured: bool
    aura_connected: bool
    speech_enabled: bool = False
    speech_configured: bool = False
    embedding_provider: str = "sentence_transformers"
    azure_openai_configured: bool = False
    readiness: dict[str, Any] = Field(default_factory=dict)
