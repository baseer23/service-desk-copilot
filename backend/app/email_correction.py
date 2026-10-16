"""Atomic structured email collection and incremental correction."""

from __future__ import annotations
import re
import secrets
import time
from dataclasses import dataclass, field
from threading import RLock
from typing import Literal
from pydantic import BaseModel, Field


class EmailOperation(BaseModel):
    operation: Literal[
        "replace_username",
        "replace_substring",
        "replace_character",
        "insert_character",
        "remove_character",
        "change_domain",
        "undo",
        "restart",
        "confirm",
        "clarify",
    ]
    target: Literal["username", "domain"] = "username"
    expected: str | None = Field(default=None, max_length=120)
    replacement: str | None = Field(default=None, max_length=120)
    position: str | int | None = None
    count: int = Field(default=1, ge=1, le=20)
    clarification: str | None = Field(default=None, max_length=300)


class EmailInterpretation(BaseModel):
    intent: Literal["correct_username", "change_domain", "confirm", "undo", "restart", "clarify"]
    operation: Literal[
        "replace_username",
        "replace_substring",
        "replace_character",
        "insert_character",
        "remove_character",
        "change_domain",
        "undo",
        "restart",
        "confirm",
        "clarify",
    ]
    old_value: str | None = Field(default=None, max_length=120)
    new_value: str | None = Field(default=None, max_length=120)
    position: str | None = Field(default=None, max_length=30)
    domain_change_requested: bool = False
    needs_clarification: bool = False
    clarification: str | None = Field(default=None, max_length=300)

    def validated_operation(self) -> EmailOperation:
        operation = "clarify" if self.needs_clarification else self.operation
        if self.domain_change_requested and operation != "change_domain":
            raise ValueError("Domain changes must use change_domain")
        return EmailOperation(
            operation=operation,
            target="domain" if operation == "change_domain" else "username",
            expected=self.old_value,
            replacement=self.new_value,
            position=self.position,
            clarification=self.clarification,
        )


@dataclass(frozen=True)
class EmailSnapshot:
    username: str
    domain: str
    domain_locked: bool
    username_confirmed: bool
    email_confirmed: bool


@dataclass
class EmailSession:
    session_id: str
    username: str = ""
    domain: str = ""
    domain_locked: bool = False
    username_confirmed: bool = False
    email_confirmed: bool = False
    pending_clarification: str | None = None
    correction_history: list[EmailSnapshot] = field(default_factory=list)
    conversation_context: list[dict[str, str]] = field(default_factory=list)
    version: int = 0
    email_mode: bool = False
    processed_requests: dict[str, dict] = field(default_factory=dict)
    touched_at: float = field(default_factory=time.monotonic)
    lock: RLock = field(default_factory=RLock, repr=False)

    @property
    def email(self):
        return f"{self.username}@{self.domain}" if self.username and self.domain else None

    @property
    def confirmed(self):
        return self.email_confirmed

    def snapshot(self):
        return EmailSnapshot(
            self.username,
            self.domain,
            self.domain_locked,
            self.username_confirmed,
            self.email_confirmed,
        )


class EmailSessions:
    def __init__(self, ttl_seconds=1800):
        self._items = {}
        self._lock = RLock()
        self.ttl_seconds = ttl_seconds

    def create(self):
        with self._lock:
            now = time.monotonic()
            self._items = {
                k: v for k, v in self._items.items() if now - v.touched_at < self.ttl_seconds
            }
            value = EmailSession(secrets.token_urlsafe(18))
            self._items[value.session_id] = value
            return value

    def get(self, session_id):
        with self._lock:
            value = self._items.get(session_id)
            if value and time.monotonic() - value.touched_at < self.ttl_seconds:
                value.touched_at = time.monotonic()
                return value
            self._items.pop(session_id, None)
            return None


def letters(value: str) -> str:
    repeats = {"double": 2, "triple": 3, "two": 2, "three": 3}
    out = []
    repeat = 1
    for token in re.findall(r"[a-z]+|[0-9]+", value.casefold().replace("-", " ")):
        if token in repeats:
            repeat = repeats[token]
        elif token not in {"letter", "letters", "and", "then"}:
            out.append(token * repeat)
            repeat = 1
    return "".join(out)


def speech_email(value: str):
    text = value.casefold().strip()
    match = re.search(r"\b(?:at|@)\b", text)
    if not match:
        return None
    before = re.sub(
        r"^(?:my e-?mail address is|actually i meant|it is|email is)\s+",
        "",
        text[: match.start()].strip(),
    )
    username, domain = letters(before), text[match.end() :].strip()
    domain = re.sub(r"\s+dot\s+", ".", domain)
    domain = domain.strip(" .,!?")
    if "." in domain:
        domain = re.sub(r"\s+", "", domain)
    return (username, domain) if re.fullmatch(r"[a-z0-9._+-]{1,120}", username) and domain else None


def deterministic_operation(message: str):
    text = message.strip().casefold()
    if re.fullmatch(r"(?:yes|yeah|correct|that'?s (?:right|correct)|confirm)", text):
        return EmailOperation(operation="confirm")
    if re.search(r"\b(?:undo|go back to the previous version)\b", text):
        return EmailOperation(operation="undo")
    if "start" in text and ("username" in text or "before" in text):
        return EmailOperation(operation="restart")
    m = re.search(r"(?:change|set) (?:the )?domain to (.+?)(?:,| but |$)", text)
    if m:
        return EmailOperation(operation="change_domain", target="domain", replacement=m.group(1))
    m = re.search(r"(?:change|replace)\s+([a-z0-9._+-]+)\s+(?:to|with)\s+(.+?)(?:[.]|$)", text)
    if m:
        return EmailOperation(
            operation="replace_substring",
            expected=letters(m.group(1)),
            replacement=letters(m.group(2)),
        )
    m = re.search(r"\bin\s+([a-z]+)\s+(?:it is|is)\s+(?:actually\s+)?(.+?)(?:[.]|$)", text)
    if m:
        return EmailOperation(
            operation="replace_substring",
            expected=letters(m.group(1)),
            replacement=letters(m.group(2)),
        )
    m = re.search(
        r"^(?:no,?\s*)?(?:it is|username is)\s+(.+?)(?:[.]?\s+the domain is correct[.]?)?$", text
    )
    if m:
        return EmailOperation(operation="replace_username", replacement=letters(m.group(1)))
    m = re.search(
        r"(?:remove|replace) (?:the )?([a-z]) immediately before (?:(?:the )?@|the at sign)(?: and replace it with| with) ([a-z])",
        text,
    )
    if m:
        return EmailOperation(
            operation="replace_character",
            expected=m.group(1),
            replacement=m.group(2),
            position="last",
        )
    m = re.search(r"remove (?:the )?([a-z]) and (?:insert|put) (.+)", text)
    if m:
        return EmailOperation(
            operation="replace_substring", expected=m.group(1), replacement=letters(m.group(2))
        )
    m = re.search(r"remove (?:the )?(first|second|last)?\s*([a-z])", text)
    if m:
        return EmailOperation(
            operation="remove_character", expected=m.group(2), position=m.group(1) or "last"
        )
    return None


def apply(session, operation, domain=None, *, fuzzy_domain=False):
    if operation.operation == "clarify":
        session.pending_clarification = (
            operation.clarification or "Which exact characters should I change?"
        )
        return session.pending_clarification
    if operation.operation == "confirm":
        if not session.email:
            return "I need both the username and approved company domain first."
        if not session.domain_locked:
            session.domain_locked = True
            return f"The domain is now confirmed. Please confirm the full spelling {session.email}."
        session.username_confirmed = session.email_confirmed = True
        return f"The spelling {session.email} is confirmed."
    if operation.operation == "undo":
        if not session.correction_history:
            return "There is no correction to undo."
        old = session.correction_history.pop()
        session.username, session.domain = old.username, old.domain
        session.domain_locked, session.username_confirmed = (
            old.domain_locked,
            old.username_confirmed,
        )
        session.email_confirmed = False
        return (
            f"Restored {session.email}." if session.email else "Restored the previous email state."
        )
    if operation.operation == "restart":
        session.correction_history.append(session.snapshot())
        session.username = ""
        session.username_confirmed = session.email_confirmed = False
        return "Username cleared; the approved domain remains locked. Please spell the username."
    if operation.operation == "change_domain":
        if not domain:
            return "I could not resolve that approved domain. Which company domain do you mean?"
        session.correction_history.append(session.snapshot())
        session.domain = domain
        session.domain_locked = not fuzzy_domain
        session.email_confirmed = False
        return (
            f"Domain changed to {domain}. Please confirm it."
            if fuzzy_domain
            else f"Updated to {session.email}. Is that spelling correct?"
        )
    if not session.username:
        return "Please give me the username part before the @ first."
    old = session.username
    expected = operation.expected or ""
    replacement = operation.replacement or ""
    new = old
    if operation.operation == "replace_username":
        new = replacement
    elif operation.operation == "replace_substring":
        if not expected or old.count(expected) != 1:
            session.pending_clarification = f"I cannot find one unambiguous '{expected}' in the current username. Which characters should change?"
            return session.pending_clarification
        new = old.replace(expected, replacement, 1)
    elif operation.operation in {"replace_character", "remove_character"}:
        indexes = [i for i, c in enumerate(old) if c == expected]
        pos = operation.position or "last"
        index = (
            indexes[0]
            if pos == "first" and indexes
            else indexes[1]
            if pos == "second" and len(indexes) > 1
            else indexes[-1]
            if pos == "last" and indexes
            else None
        )
        if index is None:
            session.pending_clarification = f"The current username does not have '{expected}' at that position. Which character should I change?"
            return session.pending_clarification
        new = (
            old[:index]
            + (replacement if operation.operation == "replace_character" else "")
            + old[index + 1 :]
        )
    elif operation.operation == "insert_character":
        if not isinstance(operation.position, int) or not 0 <= operation.position <= len(old):
            return "Which exact position should receive those characters?"
        new = old[: operation.position] + replacement + old[operation.position :]
    else:
        return "I could not safely apply that correction."
    if not re.fullmatch(r"[a-z0-9._+-]{1,120}", new):
        return "That correction would not form a valid email username, so I did not apply it."
    session.correction_history.append(session.snapshot())
    session.username = new
    session.username_confirmed = session.email_confirmed = False
    session.pending_clarification = None
    return f"Updated the username to {new}; the domain remains {session.domain}. Is that spelling correct?"
