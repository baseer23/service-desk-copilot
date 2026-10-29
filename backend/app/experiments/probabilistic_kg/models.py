"""Structured model output and validated graph contracts for the experiment."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# observed: stated by the cited evidence. derived: an interpretation that joins
# cited evidence (for example across sections). hypothesis: plausible but not
# established; never written as a traversable fact.
ClaimKind = Literal["observed", "derived", "hypothesis"]


def _plain(value: str, limit: int) -> str:
    return re.sub(r"\s+", " ", value or "").strip()[:limit]


class ProposedEntity(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    name: str = Field(min_length=1, max_length=240)
    entity_type: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    identifiers: dict[str, str] = Field(default_factory=dict)
    description: str = Field(default="", max_length=600)
    evidence_ids: list[str] = Field(default_factory=list, max_length=50)
    confidence: float = Field(default=0.0, ge=0, le=1)
    ambiguity: str | None = Field(default=None, max_length=400)

    @field_validator("name", "entity_type", "description")
    @classmethod
    def clean(cls, value: str) -> str:
        return _plain(value, 600)


class ProposedRelationship(BaseModel):
    subject_id: str
    predicate: str = Field(min_length=1, max_length=80)
    object_id: str
    kind: ClaimKind
    evidence_ids: list[str] = Field(default_factory=list, max_length=50)
    confidence: float = Field(default=0.0, ge=0, le=1)
    rationale: str = Field(default="", max_length=600)
    ambiguity: str | None = Field(default=None, max_length=400)

    @field_validator("predicate")
    @classmethod
    def clean_predicate(cls, value: str) -> str:
        value = re.sub(r"[^A-Z0-9_]+", "_", value.upper()).strip("_")
        if not value:
            raise ValueError("Predicate must contain letters or numbers")
        return value[:64]


class ProposedFormReading(BaseModel):
    """The model's reading of one form answer; checked against the extraction."""

    field_evidence_id: str
    selected_option: str | None = None


class ProposedContradiction(BaseModel):
    description: str = Field(min_length=1, max_length=600)
    evidence_ids: list[str] = Field(min_length=2, max_length=10)


class ProposedEntityLink(BaseModel):
    """Same-identity link from a proposed entity to an entity already in the graph."""

    entity_id: str
    existing_entity_key: str = Field(min_length=1, max_length=300)
    reason: str = Field(default="", max_length=400)


class ProbabilisticGraphProposal(BaseModel):
    document_id: str = Field(min_length=1, max_length=256)
    document_type: str = Field(default="Document", max_length=160)
    summary: str = Field(default="", max_length=2000)
    concepts: list[str] = Field(default_factory=list, max_length=50)
    entities: list[ProposedEntity] = Field(default_factory=list)
    relationships: list[ProposedRelationship] = Field(default_factory=list)
    form_readings: list[ProposedFormReading] = Field(default_factory=list)
    contradictions: list[ProposedContradiction] = Field(default_factory=list)
    entity_links: list[ProposedEntityLink] = Field(default_factory=list)

    @model_validator(mode="after")
    def references_known_entities(self):
        ids = {entity.id for entity in self.entities}
        if len(ids) != len(self.entities):
            raise ValueError("Entity IDs must be unique")
        for relation in self.relationships:
            for value in (relation.subject_id, relation.object_id):
                # "existing:<key>" names an entity already in the graph (from
                # existing_entities in the request), not one proposed here.
                if value not in ids and not value.startswith(EXISTING_PREFIX):
                    raise ValueError("Relationship references an unknown proposed entity")
        return self


EXISTING_PREFIX = "existing:"


class ValidatedEntity(BaseModel):
    key: str  # tenant-independent canonical identity, e.g. "organization:harbour logistics inc"
    name: str
    entity_type: str
    aliases: list[str] = Field(default_factory=list)
    identifiers: dict[str, str] = Field(default_factory=dict)
    description: str = ""
    document_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ValidatedClaim(BaseModel):
    id: str
    subject_key: str
    predicate: str
    object_key: str
    status: ClaimKind
    proposed_kind: ClaimKind
    document_id: str
    evidence_ids: list[str]
    confidence: float
    rationale: str = ""
    contested: bool = False

    @property
    def verified(self) -> bool:
        return self.status in {"observed", "derived"}


class Rejection(BaseModel):
    document_id: str
    item_type: Literal["entity", "relationship", "form_reading", "contradiction", "entity_link"]
    item: dict
    reason: str


class ValidatedContradiction(BaseModel):
    id: str
    description: str
    claim_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str]
    source: Literal["model", "deterministic"]


class ValidatedGraph(BaseModel):
    entities: dict[str, ValidatedEntity] = Field(default_factory=dict)
    claims: list[ValidatedClaim] = Field(default_factory=list)
    contradictions: list[ValidatedContradiction] = Field(default_factory=list)
    rejections: list[Rejection] = Field(default_factory=list)
    demotions: list[dict] = Field(default_factory=list)
    form_readings_checked: int = 0
    form_readings_rejected: int = 0
