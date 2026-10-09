from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StructuredModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TicketAnalysis(StructuredModel):
    user_name: str | None = Field(
        default=None,
        description="Full name of the affected user explicitly named in the ticket summary.",
    )
    company: str | None = Field(
        default=None, description="Organization explicitly named in the ticket summary."
    )
    site: str | None = None
    device: str | None = None
    product: str | None = None
    issue_type: str = Field(min_length=1)
    symptoms: str = Field(min_length=1)
    error_messages: list[str] = Field(default_factory=list)
    affected_scope: str | None = None
    entities: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    initial_hypotheses: list[str] = Field(default_factory=list)


class ToolRequest(StructuredModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class InvestigationDecision(StructuredModel):
    hypotheses: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    evidence_summary: str
    missing_information: list[str] = Field(default_factory=list)
    requested_tools: list[ToolRequest] = Field(default_factory=list, max_length=5)
    question_for_human: str | None = Field(
        default=None,
        description="Question requiring a human answer, or null when no human input is needed.",
    )
    human_field: Literal["current_site", "required_update_installed", "other"] | None = Field(
        default=None,
        description="The field answered by question_for_human. Null when there is no question.",
    )
    needs_web_research: bool = False
    enough_information: bool
    likely_issue: str | None = None
    recommended_next_action: str | None = None
    confidence: float = Field(ge=0, le=1)
    uncertainty: list[str] = Field(default_factory=list)
    decision_justification: str = ""

    @model_validator(mode="after")
    def valid_question(self):
        if not self.question_for_human:
            self.question_for_human = None
            self.human_field = None
        elif not self.human_field:
            raise ValueError("Human questions require a human_field")
        return self


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
    def valid_timestamp(cls, value):
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value
