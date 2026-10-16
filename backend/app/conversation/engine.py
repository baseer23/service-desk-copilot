from __future__ import annotations
import uuid
from fastapi import HTTPException
from backend.app.conversation.interpreter import deterministic_turn
from backend.app.conversation.models import InterpretedTurn
from backend.app.conversation.operations import apply_operation, spell_email
from backend.app.models.provider import LLMError


class ConversationEngine:
    def __init__(self, harness, provider, provider_name):
        self.harness = harness
        self.provider = provider
        self.provider_name = provider_name

    async def process(self, session, summary, request_id=None, expected_version=None):
        request_id = request_id or uuid.uuid4().hex
        with session.lock:
            if request_id in session.processed_requests:
                return session.processed_requests[request_id]
            if expected_version is not None and expected_version != session.version:
                raise HTTPException(
                    409, "This turn used an older conversation state. Please retry."
                )
            version = session.version
        turn = deterministic_turn(summary, session.email, session.active_entity)
        if turn is None:
            try:
                turn = await self.provider.generate_structured(
                    "conversation_interpret",
                    {
                        "transcript": summary,
                        "active_entity": session.active_entity,
                        "entities": session.entities,
                        "pending_clarification": session.pending_clarification,
                    },
                    InterpretedTurn,
                )
            except LLMError as exc:
                raise HTTPException(
                    502,
                    f"{self.provider_name} could not interpret that correction. Your current information was preserved.",
                ) from exc
        resolutions = {}
        for op in turn.operations:
            if op.entity == "contact.email.domain" and op.operation == "SET":
                result = await self.harness.invoke(
                    "resolve_entity",
                    {
                        "entity_type": op.entity,
                        "candidate": op.new_value or "",
                        "current_value": session.entities.get(op.entity),
                    },
                )
                if result.get("status") != "ok":
                    raise HTTPException(
                        503,
                        "I couldn't check that domain right now. Your current information was preserved.",
                    )
                resolutions[id(op)] = result["result"]
        with session.lock:
            if session.version != version:
                raise HTTPException(409, "A newer turn was already applied. Please retry.")
            prior = session.email
            response = None
            if turn.needs_clarification:
                response = turn.clarification or "Which exact characters should I change?"
            for op in turn.operations:
                resolution = resolutions.get(id(op))
                if resolution and resolution["clarification_required"]:
                    response = "Which domain did you mean?"
                    continue
                response = apply_operation(session, op, resolution) or response
                session.active_entity = (
                    "contact.email" if op.entity.startswith("contact.email.") else op.entity
                )
            confirmed = {
                "contact.email.local_part",
                "contact.email.domain",
            } <= session.confirmed_fields
            email_confirmation = confirmed and any(
                op.operation == "CONFIRM" and op.entity.startswith("contact.email.")
                for op in turn.operations
            )
            if email_confirmation:
                session.active_entity = None
                response = (
                    "Thanks. Do you have your ticket number?"
                    if session.active_helpdesk_task == "ticket"
                    else "Thanks. How can I help with your support request?"
                )
            elif session.email and not response:
                response = f"I have {spell_email(session.email)}. Is that right?"
            elif session.active_entity == "ticket.reference" and not response:
                response = (
                    f"I have ticket {session.entities.get('ticket.reference')}. Is that right?"
                )
            session.version += 1
            session.pending_clarification = turn.clarification if turn.needs_clarification else None
            session.conversation_history.append({"role": "user", "content": summary})
            session.conversation_history = session.conversation_history[-16:]
            result = {
                "session_id": session.session_id,
                "response": response or "Please clarify that.",
                "mode": "email"
                if session.active_entity == "contact.email" or confirmed
                else "helpdesk",
                "email": session.email,
                "email_confirmed": confirmed,
                "confirmed": confirmed,
                "version": session.version,
                "provider": self.provider_name,
                "operation": turn.operations[0].operation if turn.operations else None,
                "previous_email": prior,
            }
            session.processed_requests[request_id] = result
            if len(session.processed_requests) > 64:
                session.processed_requests.pop(next(iter(session.processed_requests)))
            return result
