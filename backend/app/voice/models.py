"""Browser voice conversation, application decisions and versioned wire envelopes."""

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.agent.models import EvidenceItem, ToolRequest


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class VoiceStatus(str, Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    LISTENING = "listening"
    USER_SPEAKING = "user_speaking"
    PROCESSING = "processing"
    USING_TOOLS = "using_tools"
    ASSISTANT_SPEAKING = "assistant_speaking"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


TERMINAL = {VoiceStatus.COMPLETED, VoiceStatus.FAILED, VoiceStatus.STOPPED}
FIELDS = ("name", "email", "callback_number")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class TranscriptTurn(StrictModel):
    id: str
    role: Literal["user", "assistant", "tool", "system_event"]
    text: str = Field(default="", max_length=8000)
    state: Literal["partial", "final", "interrupted"] = "final"
    created_at: str = Field(default_factory=utcnow)


class ContactCandidate(StrictModel):
    value: str = Field(max_length=300)
    raw_quote: str = Field(max_length=1000)
    confidence: float = Field(default=0.5, ge=0, le=1)
    turn_id: str = ""


class PendingConfirmation(StrictModel):
    field: Literal["name", "email", "callback_number"]
    value: str
    revision: int


class TroubleshootingAction(StrictModel):
    id: str
    instruction: str
    evidence_ids: list[str]
    result: Literal["unconfirmed", "worked", "failed"] = "unconfirmed"
    result_turn_id: str | None = None


class VoiceConversation(StrictModel):
    session_id: str
    tenant_id: str
    owner_id: str
    mode: Literal["voice", "text"] = "voice"
    case_id: str | None = None
    status: VoiceStatus = VoiceStatus.IDLE
    started_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)
    ended_at: str | None = None
    provider_session_id: str | None = None
    turns: list[TranscriptTurn] = Field(default_factory=list)
    candidates: dict[str, ContactCandidate] = Field(default_factory=dict)
    confirmed_profile: dict[str, str] = Field(default_factory=dict)
    profile_region: str | None = None
    pending_confirmation: PendingConfirmation | None = None
    confirmation_retries: dict[str, int] = Field(default_factory=dict)
    manual_contact_fields: list[str] = Field(default_factory=list)
    issue_summary: str = ""
    issue_details: dict[str, str] = Field(default_factory=dict)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    actions: list[TroubleshootingAction] = Field(default_factory=list)
    resolution_status: Literal["unconfirmed", "user_confirmed_resolved", "escalation_required"] = (
        "unconfirmed"
    )
    final_summary: str = ""
    failure_category: str | None = None
    version: int = 0

    def public(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"owner_id", "provider_session_id"})


class VoiceEvent(StrictModel):
    id: str
    type: str
    text: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class ClientEnvelope(StrictModel):
    version: Literal[1] = 1
    type: Literal[
        "audio.append", "audio.commit", "text.input", "response.cancel", "session.stop", "ping"
    ]
    session_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    sequence: int = Field(gt=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class ServerEnvelope(StrictModel):
    version: Literal[1] = 1
    type: str
    session_id: str
    sequence: int
    payload: dict[str, Any] = Field(default_factory=dict)


class SessionStart(StrictModel):
    mode: Literal["voice", "text"] = "voice"
    resume_session_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class VoiceExtraction(StrictModel):
    profile: dict[str, ContactCandidate] = Field(default_factory=dict)
    region: str | None = None
    issue_summary: str | None = Field(default=None, max_length=2000)
    issue_details: dict[str, str] = Field(default_factory=dict)


class VoicePlan(StrictModel):
    tool_calls: list[ToolRequest] = Field(default_factory=list, max_length=4)


class VoiceTurnProposal(StrictModel):
    extraction: VoiceExtraction
    tool_calls: list[ToolRequest] = Field(default_factory=list, max_length=4)
    clarification_question: str | None = Field(default=None, max_length=300)


class VoiceReply(StrictModel):
    reply: str = Field(min_length=1, max_length=1000)
    question: str | None = Field(default=None, max_length=300)
    action: str | None = Field(default=None, max_length=600)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    requires_authorization: bool = False
    escalation_required: bool = False


class VoiceGreeting(StrictModel):
    reply: str = Field(min_length=1, max_length=500)
