from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StructuredModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class RequestIntent(StructuredModel):
    in_scope: bool = True
    workflow: Literal["knowledge", "service_desk"]
    knowledge_source: Literal["documents", "general", "public_web"] = "documents"
    public_topic: Literal["none", "news", "web"] = "none"
    goal: str
    justification: str


class GeneralAnswer(StructuredModel):
    answer: str = Field(min_length=1, max_length=6000)
    uncertainty: list[str] = Field(default_factory=list)


class TicketAnalysis(StructuredModel):
    user_name: str | None = None
    company: str | None = None
    site: str | None = None
    device: str | None = None
    product: str | None = None
    issue_type: str
    symptoms: str
    error_messages: list[str] = Field(default_factory=list)
    affected_scope: str | None = None
    entities: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    initial_hypotheses: list[str] = Field(default_factory=list)


class ToolRequest(StructuredModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class SkippedSource(StructuredModel):
    source: str
    reason: str


class InvestigationPlan(StructuredModel):
    goal: str
    additional_evidence_useful: bool = True
    source_strategy: str = ""
    known_facts: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    tool_calls: list[ToolRequest] = Field(default_factory=list, max_length=6)
    parallel_groups: list[list[int]] = Field(default_factory=list)
    skipped_sources: list[SkippedSource] = Field(default_factory=list)
    clarification_needed: bool = False
    clarification_question: str | None = None
    clarification_reason: str | None = None
    stop_condition: str

    @model_validator(mode="after")
    def groups_valid(self):
        indexes = [i for group in self.parallel_groups for i in group]
        if len(indexes) != len(set(indexes)) or any(
            i < 0 or i >= len(self.tool_calls) for i in indexes
        ):
            raise ValueError("Parallel groups must contain unique valid tool indexes")
        # The model may search before requesting clarification, but a
        # clarification-only plan must carry a real question and rationale.
        if self.clarification_needed and not self.tool_calls:
            if not (self.clarification_question or "").strip():
                raise ValueError("Clarification-only plans require a question")
            if not (self.clarification_reason or "").strip():
                raise ValueError("Clarification-only plans require a rationale")
        return self


class RejectedEvidence(StructuredModel):
    id: str
    reason: str


class EvidenceGradeResult(StructuredModel):
    accepted_ids: list[str] = Field(default_factory=list)
    rejected: list[RejectedEvidence] = Field(default_factory=list)


class EvidenceItem(StructuredModel):
    source_quality: Literal["primary", "third_party", "internal", "fixture", "unclassified"] = (
        "unclassified"
    )
    id: str
    source_system: str
    source_type: Literal[
        "confirmed_fact",
        "human_confirmation",
        "human_report",
        "public_documentation",
        "document",
        "system_record",
        "tool_error",
    ]
    tool: str
    timestamp: str
    observed_at: str
    source: str
    content: dict[str, Any]
    entities: list[str] = Field(default_factory=list)
    relevance: float = Field(default=1, ge=0, le=1)
    reliability: float = Field(ge=0, le=1)
    case_id: str | None = None
    url: str | None = None
    doc_id: str | None = None


class Hypothesis(StructuredModel):
    claim: str
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)
    status: Literal["plausible", "supported", "rejected"] = "plausible"


class MissingInformation(StructuredModel):
    description: str
    affects_decision: bool = True


class InvestigationDecision(StructuredModel):
    outcome: Literal["answer", "ask_one_question", "cannot_verify"] = "cannot_verify"
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    evidence_summary: str
    missing_information: list[MissingInformation] = Field(default_factory=list)
    requested_tools: list[ToolRequest] = Field(default_factory=list, max_length=6)
    question_for_human: str | None = None
    human_field: str | None = None
    question_subject: str | None = None
    question_reason: str | None = None
    needs_web_research: bool = False
    research_queries: list[str] = Field(default_factory=list, max_length=3)
    preferred_domains: list[str] = Field(default_factory=list, max_length=5)
    enough_information: bool
    likely_issue: str | None = None
    answer: str | None = None
    recommended_next_action: str | None = None
    troubleshooting_steps: list[str] = Field(default_factory=list)
    escalation_required: bool = False
    confidence: float = Field(ge=0, le=1)
    uncertainty: list[str] = Field(default_factory=list)
    decision_justification: str = ""

    @field_validator("hypotheses", mode="before")
    @classmethod
    def hypotheses_typed(cls, v):
        return [{"claim": x} if isinstance(x, str) else x for x in v]

    @field_validator("missing_information", mode="before")
    @classmethod
    def missing_typed(cls, v):
        return [{"description": x} if isinstance(x, str) else x for x in v]

    @model_validator(mode="after")
    def question_valid(self):
        if not self.question_for_human:
            self.question_for_human = self.human_field = None
            if self.outcome == "ask_one_question":
                raise ValueError("ask_one_question requires a question_for_human")
        elif not self.human_field:
            raise ValueError("Human questions require a human_field")
        else:
            self.outcome = "ask_one_question"
        if self.enough_information and self.answer:
            self.outcome = "answer"
        return self


class ResearchSelection(StructuredModel):
    directly_relevant: bool = False
    selected_urls: list[str] = Field(default_factory=list, max_length=4)
    evidence_gap: str
    justification: str


class GraphLookupPlan(StructuredModel):
    entities: list[str] = Field(default_factory=list, max_length=10)
    concepts: list[str] = Field(default_factory=list, max_length=10)
    relationship_types: list[Literal[
        "HAS_OBJECT", "HAS_SECTION", "HAS_CHUNK", "HAS_TABLE", "HAS_FIGURE",
        "REFERENCES_TABLE", "REFERENCES_FIGURE", "MENTIONS", "RELATED_TO",
    ]] = Field(default_factory=list, max_length=10)
    document_ids: list[str] = Field(default_factory=list, max_length=10)
    object_types: list[Literal["Chunk", "Table", "Figure", "Section"]] = Field(
        default_factory=lambda: ["Chunk", "Table", "Figure", "Section"]
    )
    limit: int = Field(default=20, ge=1, le=20)


class VerificationResult(StructuredModel):
    # Independent judgement about a proposed *question*, not the truth of an
    # answer the user has not supplied. None preserves older provider behavior.
    clarification_valid: bool | None = None
    supported_claims: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    should_continue_research: bool
    justification: str


class FinalResponse(StructuredModel):
    issue_summary: str
    answer: str | None = None
    likely_causes: list[Hypothesis] = Field(default_factory=list)
    confirmed_facts: list[dict[str, Any]] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    actions_already_taken: list[str] = Field(default_factory=list)
    recommended_next_action: str | None = None
    troubleshooting_steps: list[str] = Field(default_factory=list)
    questions_remaining: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    uncertainty: list[str] = Field(default_factory=list)
    escalation_required: bool = False
    sources: list[str] = Field(default_factory=list)


class MemoryFact(StructuredModel):
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    value: str = Field(min_length=1)
    source: Literal["human_confirmed", "trusted_tool", "resolved_outcome"]
    source_system: str
    observed_at: str
    confidence: float = Field(ge=0, le=1)
    reliability: Literal["confirmed"] = "confirmed"
    case_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("observed_at")
    @classmethod
    def timestamp_valid(cls, v):
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


class CapabilityResult(StructuredModel):
    tool: str
    source_system: str
    timestamp: str
    reliability: float = Field(ge=0, le=1)
    status: Literal["ok", "unavailable", "error"] = "ok"
    error: str | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)
    result: Any = None
