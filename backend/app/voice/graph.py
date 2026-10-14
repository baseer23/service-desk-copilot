"""Compiled bounded support loop. Graph tools use only the discovered MCP boundary."""

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.app.agent.models import EvidenceItem, InvestigationPlan, RequestIntent
from backend.app.agent.scope import SCOPE_MESSAGE
from backend.app.models.provider import CaseReasoningProvider
from backend.app.services.source_policy import permits, quality

from .models import (
    ContactCandidate,
    PendingConfirmation,
    TroubleshootingAction,
    VoiceConversation,
    VoiceGreeting,
    VoiceReply,
    VoiceStatus,
    VoiceTurnProposal,
)
from .normalization import (
    action_result,
    confirmation,
    grouped_phone,
    normalize_email,
    normalize_name,
    normalize_phone,
    spoken_email,
)

PROMPT = (Path(__file__).parent / "prompts/support_v1.md").read_text()
GREETING = "Hello, I'm DeskMate, your AI support assistant. What problem can I help you with today?"
SAFE_FAILURE = (
    "I could not verify enough evidence for a safe next step. I recommend escalating this issue to"
    " your service desk."
)


class TurnState(TypedDict, total=False):
    conversation: VoiceConversation
    text: str
    turn_id: str
    context: dict[str, Any]
    reply: str
    skip: bool
    proposal: VoiceTurnProposal


class VoiceTurnSuperseded(Exception):
    """User speech superseded this turn; abandon further paid work and output."""


class VoiceSupportGraph:
    def __init__(
        self, settings: Any, harness: Any, provider: CaseReasoningProvider, activity: Any = None,
        is_current: Callable[[], bool] = lambda: True,
    ):
        self.settings, self.harness, self.provider, self.activity = (
            settings,
            harness,
            provider,
            activity,
        )
        self.is_current = is_current
        graph = StateGraph(TurnState)
        for name, fn in [
            ("extract", self.extract),
            ("investigate", self.investigate),
            ("respond", self.respond),
        ]:
            graph.add_node(name, fn)
        graph.add_edge(START, "extract")
        graph.add_edge("extract", "investigate")
        graph.add_edge("investigate", "respond")
        graph.add_edge("respond", END)
        self.compiled = graph.compile()

    async def generate(self, stage: str, context: dict[str, Any], schema: Any) -> Any:
        if stage != "voice_greeting":
            self.ensure_current()
        result = await asyncio.wait_for(
            self.provider.generate_structured(stage, {"instructions": PROMPT, **context}, schema),
            self.settings.model_timeout_sec * 2
            + self.settings.azure_reasoning_min_interval_seconds
            + 5,
        )
        if stage != "voice_greeting":
            self.ensure_current()
        return result

    def ensure_current(self) -> None:
        if not self.is_current():
            raise VoiceTurnSuperseded()

    async def greeting(self) -> str:
        response = await self.generate("voice_greeting", {}, VoiceGreeting)
        return response.reply

    async def run(
        self, state: VoiceConversation, text: str, turn_id: str
    ) -> tuple[VoiceConversation, str]:
        result = await self.compiled.ainvoke(
            {"conversation": state.model_copy(deep=True), "text": text, "turn_id": turn_id},
            config={"recursion_limit": 8},
        )
        return result["conversation"], result["reply"]

    async def extract(self, state: TurnState) -> dict[str, Any]:
        c = state["conversation"]
        text = state["text"]
        explicit = confirmation(text)
        if c.pending_confirmation and explicit is not None:
            pending = c.pending_confirmation
            if explicit:
                c.confirmed_profile[pending.field] = pending.value
                c.confirmation_retries.pop(pending.field, None)
            else:
                c.candidates.pop(pending.field, None)
                c.confirmed_profile.pop(pending.field, None)
                c.confirmation_retries[pending.field] = (
                    c.confirmation_retries.get(pending.field, 0) + 1
                )
            c.pending_confirmation = None
        elif not c.pending_confirmation and c.actions and c.actions[-1].result == "unconfirmed":
            last_assistant = next((t.text for t in reversed(c.turns) if t.role == "assistant"), "")
            bare = text.strip().lower().strip(".!?") in {"yes", "no"}
            result = (
                action_result(text)
                if not bare or last_assistant.endswith("Did that work?")
                else None
            )
            if result:
                c.actions[-1].result = result
                c.actions[-1].result_turn_id = state["turn_id"]
                if result == "worked":
                    c.resolution_status = "user_confirmed_resolved"
                    c.final_summary = (
                        c.issue_summary
                        + " — User confirmed success after: "
                        + c.actions[-1].instruction
                    )
                    c.status = VoiceStatus.COMPLETED
                    return {
                        "conversation": c,
                        "skip": True,
                        "reply": "You confirmed that the step worked. "
                        + c.final_summary
                        + " You can save the case summary below.",
                    }
        context = {
            "current_turn": text,
            "recent_turns": [t.model_dump() for t in c.turns[-12:]],
            "confirmed_profile": c.confirmed_profile,
            "candidates": {k: v.value for k, v in c.candidates.items()},
            "issue_summary": c.issue_summary,
            "issue_details": c.issue_details,
            "actions": [a.model_dump() for a in c.actions[-8:]],
            "permitted_tools": self.harness.schemas("voice"),
        }
        if self.settings.knowledge_scope == "support":
            intent = await self.provider.generate_structured(
                "classify_intent",
                {
                    "request": text,
                    "conversation_issue": c.issue_summary,
                    "continuation": bool(c.turns or c.pending_confirmation),
                    "knowledge_scope": "support",
                },
                RequestIntent,
            )
            if not intent.in_scope:
                return {"conversation": c, "skip": True, "reply": SCOPE_MESSAGE}
        turn_proposal = await self.generate("voice_turn", context, VoiceTurnProposal)
        extraction = turn_proposal.extraction
        if (
            extraction.region
            and extraction.region.upper() in __import__("phonenumbers").SUPPORTED_REGIONS
        ):
            c.profile_region = extraction.region.upper()
        if extraction.issue_summary and len(extraction.issue_summary) <= 2000:
            c.issue_summary = extraction.issue_summary
        c.issue_details.update(
            {
                k: v[:1000]
                for k, v in extraction.issue_details.items()
                if k
                in {
                    "company",
                    "site",
                    "scope",
                    "last_worked",
                    "error",
                    "attempts",
                    "impact",
                    "urgency",
                }
                and isinstance(v, str)
            }
        )
        for field, proposal in extraction.profile.items():
            if (
                field not in {"name", "email", "callback_number"}
                or not proposal.raw_quote
                or proposal.raw_quote.casefold() not in text.casefold()
            ):
                continue
            try:
                # The model cannot invent a value that was absent from the raw turn.
                value = (
                    normalize_name(proposal.raw_quote)
                    if field == "name"
                    else (
                        normalize_email(proposal.raw_quote)
                        if field == "email"
                        else normalize_phone(proposal.raw_quote, c.profile_region)
                    )
                )
            except ValueError:
                c.confirmation_retries[field] = c.confirmation_retries.get(field, 0) + 1
                if (
                    field == "callback_number"
                    and not c.profile_region
                    and not proposal.raw_quote.strip().startswith("+")
                ):
                    return {
                        "conversation": c,
                        "context": context,
                        "skip": True,
                        "reply": (
                            "Which country is the callback number in? You can type the country and"
                            " number below."
                        ),
                    }
                continue
            if c.confirmed_profile.get(field) == value:
                continue
            c.confirmed_profile.pop(field, None)
            if field in c.manual_contact_fields:
                c.manual_contact_fields.remove(field)
                c.confirmation_retries.pop(field, None)
            c.candidates[field] = ContactCandidate(
                value=value,
                raw_quote=proposal.raw_quote,
                confidence=proposal.confidence,
                turn_id=state["turn_id"],
            )
            if c.pending_confirmation and c.pending_confirmation.field == field:
                c.pending_confirmation = None
        context.update(
            issue_summary=c.issue_summary,
            issue_details=c.issue_details,
            confirmed_profile=c.confirmed_profile,
        )
        for field in ("name", "email", "callback_number"):
            if field in c.confirmed_profile or field in c.manual_contact_fields:
                continue
            if c.confirmation_retries.get(field, 0) >= self.settings.voice_max_confirmation_retries:
                c.manual_contact_fields.append(field)
                c.resolution_status = "escalation_required"
                return {
                    "conversation": c,
                    "context": context,
                    "skip": True,
                    "reply": "Recognition could not reliably confirm your "
                    + field.replace("_", " ")
                    + ". Please use typed correction; otherwise the service desk will need to"
                    " confirm it manually.",
                }
            if field in c.candidates:
                value = c.candidates[field].value
                c.pending_confirmation = PendingConfirmation(
                    field=field, value=value, revision=c.version
                )
                c.status = VoiceStatus.AWAITING_CONFIRMATION
                # Non-answer turns consume a bounded confirmation budget too.
                c.confirmation_retries[field] = c.confirmation_retries.get(field, 0) + 1
                spoken = (
                    spoken_email(value)
                    if field == "email"
                    else grouped_phone(value) if field == "callback_number" else value
                )
                return {
                    "conversation": c,
                    "context": context,
                    "skip": True,
                    "reply": "I heard your "
                    + field.replace("_", " ")
                    + " as "
                    + spoken
                    + ". Is that correct?",
                }
        return {"conversation": c, "context": context, "skip": False, "proposal": turn_proposal}

    async def investigate(self, state: TurnState) -> dict[str, Any]:
        self.ensure_current()
        if state.get("skip"):
            return {}
        c = state["conversation"]
        validated = InvestigationPlan(
            goal="Voice support evidence", tool_calls=state["proposal"].tool_calls,
            stop_condition="One response"
        )
        self.harness.validate_plan(validated, "voice")
        for call in validated.tool_calls:
            self.ensure_current()
            if call.name == "fetch_web_page" and not permits(
                str(call.arguments.get("url", "")), self.settings
            ):
                continue
            if self.activity:
                await self.activity(
                    "Checking previous incidents"
                    if call.name == "search_previous_tickets"
                    else "Searching knowledge"
                )
            try:
                result = await asyncio.wait_for(self.harness.invoke(call.name, call.arguments), 20)
                if call.name == "search_web":
                    continue  # Search snippets are discovery, never diagnostic evidence.
                items = result.get("items", [])
                if result.get("status") != "ok":
                    continue
                for item in items[:8]:
                    if call.name == "fetch_web_page" and not permits(
                        str(item.get("url", call.arguments.get("url", ""))), self.settings
                    ):
                        continue
                    identity = hashlib.sha256((call.name + str(item)).encode()).hexdigest()[:24]
                    evidence = EvidenceItem(
                        id=identity,
                        tool=call.name,
                        source_system=result["source_system"],
                        source_type=(
                            "public_documentation"
                            if call.name in {"search_web", "fetch_web_page"}
                            else "document" if call.name == "search_knowledge" else "system_record"
                        ),
                        timestamp=result["timestamp"],
                        observed_at=item.get("observed_at", result["timestamp"]),
                        source=str(item.get("source", call.name))[:500],
                        content=(
                            item
                            if len(json.dumps(item)) <= 12000
                            else {"text": json.dumps(item)[:10000], "truncated": True}
                        ),
                        reliability=result["reliability"],
                        source_quality=(
                            quality(item.get("url", ""), self.settings)
                            if call.name == "fetch_web_page"
                            else (
                                "fixture"
                                if result["source_system"] == "local_fixture"
                                else "internal"
                            )
                        ),
                    )
                    c.evidence = [e for e in c.evidence if e.id != identity] + [evidence]
            except Exception:  # noqa: BLE001 - sanitize external failures at the boundary
                if self.activity:
                    await self.activity("Evidence source unavailable")
        self.ensure_current()
        c.evidence = c.evidence[-40:]
        return {"conversation": c}

    async def respond(self, state: TurnState) -> dict[str, Any]:
        self.ensure_current()
        if state.get("skip"):
            return {}
        c = state["conversation"]
        proposal = state["proposal"]
        if not proposal.tool_calls and proposal.clarification_question:
            # A question supplies no technical action or resolution. It still
            # passes the same application safety checks as a researched reply.
            response = VoiceReply(
                reply=proposal.clarification_question, question=proposal.clarification_question
            )
        else:
            if self.activity:
                await self.activity("Verifying evidence")
            response = await self.generate(
                "voice_reply",
                {
                    **{
                        key: value for key, value in state["context"].items()
                        if key != "permitted_tools"
                    },
                    "evidence": [e.model_dump() for e in c.evidence],
                    "missing_contact": [
                        f
                        for f in ("name", "email", "callback_number")
                        if f not in c.confirmed_profile and f not in c.manual_contact_fields
                    ],
                },
                VoiceReply,
            )
        ids = {e.id for e in c.evidence if e.reliability >= 0.5 and e.source_type != "tool_error"}
        unsafe = bool(
            (response.action or response.question)
            and re.search(
                r"\b(delete|disable|password|registry|sudo|administrator|firewall|format|install)\b",
                response.action or response.question or "",
                re.I,
            )
        )
        question_action = bool(response.question and re.search(
            r"\b(?:please|can you|could you|would you|will you|try|if you)\s+"
            r"(?:please\s+)?(?:restart|reboot|reset|change|run|execute|remove|download|"
            r"update|upgrade|click|open|check|disconnect|reconnect)\b",
            response.question, re.I,
        ))
        repeated = bool(
            response.action
            and any(a.instruction.casefold() == response.action.casefold() for a in c.actions)
        )
        if (
            response.requires_authorization
            or response.escalation_required
            or unsafe
            or question_action
            or repeated
            or (
                response.action
                and (not response.evidence_ids or not set(response.evidence_ids) <= ids)
            )
        ):
            c.resolution_status = "escalation_required"
            c.final_summary = c.issue_summary + " — Escalated; resolution unconfirmed."
            return {"conversation": c, "reply": SAFE_FAILURE}
        if response.action:
            c.actions.append(
                TroubleshootingAction(
                    id=uuid.uuid4().hex,
                    instruction=response.action,
                    evidence_ids=response.evidence_ids,
                )
            )
            reply = response.action.rstrip(".?") + ". Did that work?"
        elif response.question:
            reply = response.question
        else:
            # Ungrounded prose cannot become a technical claim or a resolution.
            reply = SAFE_FAILURE
            c.resolution_status = "escalation_required"
        if reply.count("?") > 1 or "\n" in reply or (
            response.question and not reply.rstrip().endswith("?")
        ):
            reply = "What is the exact error message you see?"
        c.status = VoiceStatus.LISTENING
        return {"conversation": c, "reply": reply}
