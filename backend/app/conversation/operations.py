from __future__ import annotations
import re
from backend.app.conversation.models import ConversationSession, EntityOperation


def spell_email(value):
    local, domain = value.split("@", 1)
    return "-".join(local.upper()) + " at " + domain


def apply_operation(session: ConversationSession, op: EntityOperation, resolved=None):
    if op.operation == "UNDO":
        if not session.undo_history:
            return "There is no correction to undo."
        snap = session.undo_history.pop()
        session.entities = dict(snap.entities)
        session.confirmed_fields = set(snap.confirmed_fields)
        return (
            f"I restored {spell_email(session.email)}. Is that right?"
            if session.email
            else "I restored the previous value."
        )
    if op.operation == "CONFIRM":
        if op.entity.startswith("contact.email."):
            if not session.email:
                return "Please provide the email address first."
            session.confirmed_fields.update({"contact.email.local_part", "contact.email.domain"})
        elif op.entity not in session.entities:
            return "Please provide that information first."
        else:
            session.confirmed_fields.add(op.entity)
        return None
    if op.needs_clarification:
        session.pending_clarification = op.clarification
        return op.clarification
    current = session.entities.get(op.entity, "")
    if op.operation == "SET":
        new = op.new_value or ""
    elif op.operation == "KEEP":
        return None
    elif op.operation == "CLEAR":
        new = ""
    elif op.operation == "REPLACE":
        if not op.old_value or current.count(op.old_value) != 1:
            return f"I couldn't find one clear '{op.old_value}' in the current value. Which letters should change?"
        new = current.replace(op.old_value, op.new_value or "", 1)
    elif op.operation == "INSERT":
        if op.position is None or op.position > len(current):
            return "Where should I insert those characters?"
        new = current[: op.position] + (op.new_value or "") + current[op.position :]
    elif op.operation == "DELETE":
        if not op.old_value or current.count(op.old_value) != 1:
            return "Which exact characters should I remove?"
        new = current.replace(op.old_value, "", 1)
    else:
        return "Please clarify that correction."
    if op.entity == "contact.email.local_part" and not re.fullmatch(r"[a-z0-9._+-]{1,120}", new):
        return "Please clarify the username spelling."
    if op.entity == "contact.email.domain" and resolved:
        new = resolved["canonical_value"] or new
    if new == current:
        return None
    session.undo_history.append(session.snapshot())
    session.entities[op.entity] = new
    session.confirmed_fields.discard(op.entity)
    return None
