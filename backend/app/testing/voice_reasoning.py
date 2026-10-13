"""Explicit deterministic voice reasoning for tests and isolated demos."""

import re
from typing import Any

from backend.app.agent.models import ToolRequest
from backend.app.voice.models import ContactCandidate, VoiceExtraction, VoicePlan, VoiceReply


def generate(stage: str, context: dict[str, Any], schema: Any) -> Any:
    text = context.get("current_turn", "")
    if stage == "voice_extract":
        profile = {}
        for field, pattern in [
            ("name", r"(?:my name is|name:)\s*([^,;]+)"),
            ("email", r"(?:email(?: is)?|email:)\s*([^,;]+)"),
            ("callback_number", r"(?:number(?: is)?|callback:)\s*([^,;]+)"),
        ]:
            match = re.search(pattern, text, re.I)
            if match:
                quote = match.group(1).strip().rstrip(".")
                profile[field] = ContactCandidate(value=quote, raw_quote=quote)
        issue = (
            text
            if any(
                word in text.lower()
                for word in ["vpn", "printer", "error", "cannot", "broken", "problem"]
            )
            else None
        )
        region = (
            "CA" if "canada" in text.lower() else "US" if "united states" in text.lower() else None
        )
        return VoiceExtraction(profile=profile, issue_summary=issue, region=region)
    if stage == "voice_plan":
        return VoicePlan(
            tool_calls=(
                [
                    ToolRequest(
                        name="search_knowledge",
                        arguments={"question": context.get("issue_summary") or text},
                    )
                ]
                if context.get("issue_summary")
                else []
            )
        )
    missing = context.get("missing_contact", [])
    if missing:
        questions = {
            "name": "What is your full name?",
            "email": "What is your email address?",
            "callback_number": (
                "What callback number, including country code, should the service desk use?"
            ),
        }
        return VoiceReply(reply=questions[missing[0]], question=questions[missing[0]])
    evidence = context.get("evidence", [])
    if evidence and not context.get("actions"):
        return VoiceReply(
            reply="Check the VPN connection.",
            action="Check whether the VPN client shows connected",
            evidence_ids=[evidence[0]["id"]],
        )
    if not context.get("issue_summary"):
        return VoiceReply(
            reply="What problem can I help you with?", question="What problem can I help you with?"
        )
    return VoiceReply(reply="Please contact the service desk.", escalation_required=True)
