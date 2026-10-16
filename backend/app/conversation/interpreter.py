"""Contextual validation for model-interpreted conversation turns."""

from __future__ import annotations

import re

from backend.app.conversation.models import ConversationSession, InterpretedTurn


def contextual_issue(turn: InterpretedTurn, session: ConversationSession) -> str | None:
    """Reject unsafe or contradictory operations before any state is mutated."""
    projected = dict(session.entities)
    changed_entities: set[str] = set()

    for operation in turn.operations:
        entity = operation.entity
        current = projected.get(entity, "")
        if entity in changed_entities and operation.operation not in {"KEEP", "CONFIRM"}:
            return "I found conflicting changes. What should the final value be?"
        if operation.operation not in {"KEEP", "CONFIRM", "UNDO"}:
            changed_entities.add(entity)

        if operation.operation == "CONFIRM":
            if entity.startswith("contact.email."):
                if not session.email:
                    return "Please provide the email address first."
            elif not current:
                return "Please provide that information first."
            continue
        if operation.operation in {"UNDO", "KEEP"}:
            continue
        if operation.operation == "SET":
            if current and operation.new_value != current and not operation.complete_value:
                return "Should I replace the complete value, or only change part of it?"
            candidate = operation.new_value or ""
        elif operation.operation == "REPLACE":
            if not current or not operation.old_value or current.count(operation.old_value) != 1:
                return "Which exact existing characters should I replace?"
            candidate = current.replace(operation.old_value, operation.new_value or "", 1)
        elif operation.operation == "INSERT":
            if operation.position is None or operation.position > len(current):
                return "Where should I insert those characters?"
            candidate = (
                current[: operation.position]
                + (operation.new_value or "")
                + current[operation.position :]
            )
        elif operation.operation == "DELETE":
            if not current or not operation.old_value or current.count(operation.old_value) != 1:
                return "Which exact existing characters should I remove?"
            candidate = current.replace(operation.old_value, "", 1)
        elif operation.operation == "CLEAR":
            candidate = ""
        else:
            return "Please clarify that change."

        if (
            entity == "contact.email.local_part"
            and candidate
            and not re.fullmatch(r"[a-z0-9._+-]{1,120}", candidate)
        ):
            return "Please clarify the username spelling."
        projected[entity] = candidate

    return None
