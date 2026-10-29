"""Validated, provider-neutral contracts for document semantic graph ingestion."""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def _plain(value: str, limit: int) -> str:
    return re.sub(r"\s+", " ", value or "").strip()[:limit]


class ProposedEntity(BaseModel):
    temporary_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    name: str = Field(min_length=1, max_length=240)
    entity_type: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    identifiers: dict[str, str] = Field(default_factory=dict)
    description: str = Field(default="", max_length=1200)
    evidence_object_ids: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("name", "entity_type", "description")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return _plain(value, 1200)

    @field_validator("aliases")
    @classmethod
    def clean_aliases(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(_plain(v, 240) for v in values if _plain(v, 240)))


class ProposedRelationship(BaseModel):
    source_temporary_id: str
    target_temporary_id: str
    predicate: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1200)
    evidence_object_ids: list[str] = Field(default_factory=list, max_length=50)
    confidence: float = Field(default=0.0, ge=0, le=1)
    is_explicit: bool = True
    # observed: one cited object states it; derived: it joins cited objects;
    # ambiguous: cites an ambiguous form field; hypothesis: plausible, not
    # established. Validation may lower this, never raise it. None means the
    # model gave no label: observed when is_explicit, otherwise derived.
    status: Literal["observed", "derived", "ambiguous", "hypothesis"] | None = None
    observed_at: str | None = Field(default=None, max_length=80)

    @field_validator("predicate")
    @classmethod
    def clean_predicate(cls, value: str) -> str:
        value = re.sub(r"[^A-Z0-9_]+", "_", value.upper()).strip("_")
        if not value:
            raise ValueError("Predicate must contain letters or numbers")
        return value[:64]


class DocumentGraphProposal(BaseModel):
    document_id: str = Field(min_length=1, max_length=256)
    primary_subject: str = Field(default="", max_length=300)
    document_type: str = Field(default="Document", max_length=160)
    summary: str = Field(default="", max_length=4000)
    relevant_dates: list[str] = Field(default_factory=list, max_length=50)
    entities: list[ProposedEntity] = Field(default_factory=list)
    relationships: list[ProposedRelationship] = Field(default_factory=list)

    @model_validator(mode="after")
    def references_known_entities(self):
        ids = {entity.temporary_id for entity in self.entities}
        if len(ids) != len(self.entities):
            raise ValueError("Entity temporary IDs must be unique")
        for relation in self.relationships:
            if relation.source_temporary_id not in ids or relation.target_temporary_id not in ids:
                raise ValueError("Relationship references an unknown proposed entity")
        return self


class EntityPlacement(BaseModel):
    temporary_id: str
    decision: Literal["REUSE_EXISTING", "CREATE_NEW", "UNRESOLVED"]
    existing_entity_id: str | None = None
    reason: str = Field(min_length=1, max_length=800)

    @model_validator(mode="after")
    def decision_has_valid_target(self):
        if (self.decision == "REUSE_EXISTING") != bool(self.existing_entity_id):
            raise ValueError("Only REUSE_EXISTING requires an existing entity ID")
        return self


class GraphPlacementPlan(BaseModel):
    document_id: str
    placements: list[EntityPlacement]
