from __future__ import annotations
import time
from dataclasses import dataclass, field
from threading import RLock
from typing import Literal
from pydantic import BaseModel, Field, model_validator

EntityPath = Literal[
    "contact.email.local_part",
    "contact.email.domain",
    "ticket.reference",
    "person.name",
    "device.hostname",
]
OperationKind = Literal["SET", "REPLACE", "INSERT", "DELETE", "KEEP", "CONFIRM", "UNDO", "CLEAR"]


class EntityOperation(BaseModel):
    entity: EntityPath
    operation: OperationKind
    old_value: str | None = Field(default=None, max_length=253)
    new_value: str | None = Field(default=None, max_length=253)
    position: int | None = Field(default=None, ge=0, le=253)
    needs_clarification: bool = False
    clarification: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_operands(self):
        if self.needs_clarification:
            if not self.clarification:
                raise ValueError("clarification text is required")
            return self
        if self.operation == "SET" and not self.new_value:
            raise ValueError("SET requires new_value")
        if self.operation == "REPLACE" and (not self.old_value or not self.new_value):
            raise ValueError("REPLACE requires old_value and new_value")
        if self.operation == "INSERT" and (not self.new_value or self.position is None):
            raise ValueError("INSERT requires new_value and position")
        if self.operation == "DELETE" and not self.old_value:
            raise ValueError("DELETE requires old_value")
        return self


class InterpretedTurn(BaseModel):
    intent: Literal[
        "set_entity", "correct_entity", "confirm_entity", "reject_entity", "undo", "general"
    ]
    operations: list[EntityOperation] = Field(default_factory=list, max_length=3)
    needs_clarification: bool = False
    clarification: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_turn(self):
        if self.needs_clarification and not self.clarification:
            raise ValueError("clarification text is required")
        if self.intent != "general" and not self.operations and not self.needs_clarification:
            raise ValueError("an entity intent requires an operation or clarification")
        return self


@dataclass(frozen=True)
class Snapshot:
    entities: dict[str, str]
    confirmed_fields: frozenset[str]


@dataclass
class ConversationSession:
    session_id: str
    active_helpdesk_task: str | None = None
    active_entity: str | None = None
    entities: dict[str, str] = field(default_factory=dict)
    confirmed_fields: set[str] = field(default_factory=set)
    pending_clarification: str | None = None
    conversation_history: list[dict[str, str]] = field(default_factory=list)
    version: int = 0
    processed_requests: dict[str, dict] = field(default_factory=dict)
    undo_history: list[Snapshot] = field(default_factory=list)
    touched_at: float = field(default_factory=time.monotonic)
    lock: RLock = field(default_factory=RLock, repr=False)

    @property
    def email(self):
        local = self.entities.get("contact.email.local_part")
        domain = self.entities.get("contact.email.domain")
        return f"{local}@{domain}" if local and domain else None

    def snapshot(self):
        return Snapshot(dict(self.entities), frozenset(self.confirmed_fields))
