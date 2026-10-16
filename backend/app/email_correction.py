"""Ephemeral, validated email-correction sessions for the Voice-tab demo."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from threading import RLock
from typing import Literal

from pydantic import BaseModel, Field


class EmailOperation(BaseModel):
    operation: Literal[
        "replace_substring", "replace_character", "insert_character", "remove_character",
        "change_domain", "undo", "restart", "confirm", "clarify",
    ]
    target: Literal["username", "domain"] = "username"
    expected: str | None = Field(default=None, max_length=120)
    replacement: str | None = Field(default=None, max_length=120)
    position: str | int | None = None
    count: int = Field(default=1, ge=1, le=20)
    clarification: str | None = Field(default=None, max_length=300)


@dataclass
class EmailSession:
    session_id: str
    username: str = ""
    domain: str = ""
    confirmed: bool = False
    history: list[tuple[str, str]] = field(default_factory=list)
    recent_corrections: list[str] = field(default_factory=list)
    pending_clarification: str | None = None
    conversation: list[dict[str, str]] = field(default_factory=list)

    @property
    def email(self) -> str | None:
        return f"{self.username}@{self.domain}" if self.username and self.domain else None


class EmailSessions:
    """Replaceable in-memory store; intentionally never writes addresses to logs/disk."""

    def __init__(self) -> None:
        self._items: dict[str, EmailSession] = {}
        self._lock = RLock()

    def create(self) -> EmailSession:
        with self._lock:
            value = EmailSession(secrets.token_urlsafe(18))
            self._items[value.session_id] = value
            return value

    def get(self, session_id: str) -> EmailSession | None:
        with self._lock:
            return self._items.get(session_id)


def speech_email(value: str) -> tuple[str, str] | None:
    """Parse a spoken address shape; its domain is validated separately by MCP."""
    cleaned = value.lower().strip()
    cleaned = re.sub(r"\bdot\s+([a-z])\s+([a-z])\b", r".\1\2", cleaned)
    cleaned = re.sub(r"\bdot\s+([a-z]+)\b", r".\1", cleaned)
    cleaned = re.sub(r"\bat\b", "@", cleaned)
    cleaned = re.sub(r"\s+", "", cleaned)
    if cleaned.count("@") != 1:
        return None
    username, domain_hint = cleaned.split("@", 1)
    if not re.fullmatch(r"[a-z0-9._+-]{1,120}", username) or not domain_hint:
        return None
    return username, domain_hint


def _letters(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def deterministic_operation(message: str) -> EmailOperation | None:
    text = message.strip().lower()
    if re.fullmatch(r"(?:yes|yeah|correct|that'?s correct|confirm)", text):
        return EmailOperation(operation="confirm")
    if re.fullmatch(r"undo(?: that| the last)?(?: (\d+|one|two))? changes?", text) or text == "undo that":
        amount = re.search(r"(\d+|one|two)", text)
        count = {"one": 1, "two": 2}.get(amount.group(1), int(amount.group(1))) if amount else 1
        return EmailOperation(operation="undo", count=count)
    if "start" in text and ("username" in text or "before the @" in text or "that part" in text):
        return EmailOperation(operation="restart")
    domain = re.search(r"(?:change|set) (?:the )?domain to (.+?)(?:,| but |$)", text)
    if domain:
        return EmailOperation(operation="change_domain", target="domain", replacement=domain.group(1))
    replace = re.search(r"(?:change|replace) ([a-z0-9._+-]+) to ([a-z0-9._+-]+)(?:,| but |$)", text)
    if replace:
        return EmailOperation(operation="replace_substring", expected=replace.group(1), replacement=replace.group(2))
    before_at = re.search(r"replace (?:the )?([a-z]) immediately before (?:(?:the )?@|the at sign) with ([a-z])", text)
    if before_at:
        return EmailOperation(operation="replace_character", expected=before_at.group(1), replacement=before_at.group(2), position="last")
    remove = re.search(r"remove (?:the )?(first|second|last) ([a-z])(?: from the username)?", text)
    if remove:
        return EmailOperation(operation="remove_character", expected=remove.group(2), position=remove.group(1))
    final = re.search(r"replace (?:the )?final (\d+) characters? with ([a-z\s]+)$", text)
    if final:
        return EmailOperation(operation="replace_substring", expected="__final__", replacement=_letters(final.group(2)), position=int(final.group(1)))
    return None


def apply(session: EmailSession, operation: EmailOperation, domain: str | None = None) -> str:
    if operation.operation == "clarify":
        session.pending_clarification = operation.clarification or "Which specific correction should I make?"
        return session.pending_clarification
    if operation.operation == "confirm":
        if not session.email:
            return "I need the email address before I can confirm it."
        session.confirmed = True
        return "Thank you. Your email address has been confirmed."
    if operation.operation == "undo":
        if not session.history:
            return "There is no correction to undo."
        for _ in range(min(operation.count, len(session.history))):
            session.username, session.domain = session.history.pop()
        session.confirmed = False
        return f"Restored {session.email}." if session.email else "Restored the previous email state."
    if operation.operation == "restart":
        if session.username or session.domain:
            session.history.append((session.username, session.domain))
        session.username, session.confirmed = "", False
        return "Username cleared. Please say the part before the @."
    if operation.operation == "change_domain":
        if not domain:
            return "I could not match that company domain. Which approved company do you mean?"
        if session.username or session.domain:
            session.history.append((session.username, session.domain))
        session.domain, session.confirmed = domain, False
        return f"Updated to {session.email}. Is that correct?" if session.email else f"Domain set to {domain}."
    if not session.username:
        return "Please give me the username part of the email first."
    old = session.username
    expected, replacement = operation.expected or "", operation.replacement or ""
    if operation.operation == "replace_substring":
        if expected == "__final__":
            length = int(operation.position or 0)
            if length < 1 or length > len(old):
                return "That final-character position does not match the current username."
            session.username = old[:-length] + replacement
        elif expected not in old:
            return "That text does not match the current username, so I did not change it."
        else:
            session.username = old.replace(expected, replacement, 1)
    elif operation.operation in {"replace_character", "remove_character"}:
        matches = [index for index, char in enumerate(old) if char == expected]
        if not matches:
            return "That character does not match the current username, so I did not change it."
        position = operation.position or "last"
        index = matches[0] if position == "first" else len(old) - 1 if position == "last" else None
        if position == "second":
            index = matches[1] if len(matches) > 1 else None
        if index is None:
            return "That character position does not match the current username."
        if old[index] != expected:
            return "That character position does not match the current username."
        session.username = old[:index] + ("" if operation.operation == "remove_character" else replacement) + old[index + 1 :]
    else:
        return "I could not safely apply that correction."
    session.history.append((old, session.domain))
    session.confirmed = False
    session.recent_corrections.append(operation.operation)
    return f"Updated to {session.email}. Is that correct?" if session.email else "Username updated. Please provide the company domain."
