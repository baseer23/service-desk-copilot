"""Compile a validated graph into a fixed set of idempotent Cypher statements.

Approaches B and C execute exactly these statements; only the transport
differs. The model never writes Cypher: every statement's text is one of the
templates below, and the MCP writer refuses any other text.

Graph shape (compatible with dev's semantic graph):

    (Entity)<-[:REFERS_TO]-(GraphMention)-[:MENTIONED_IN]->(Document)
    (GraphMention)-[:SUPPORTED_BY]->(Paragraph|TableCell|FormField|...)
    (GraphClaim {status: observed|derived})-[:SUBJECT|OBJECT]->(Entity)
    (GraphClaim)-[:DOCUMENTED_IN]->(Document); (GraphClaim)-[:SUPPORTED_BY]->(evidence)
    (Entity)-[:RELATED_TO {predicate, status}]->(Entity)        verified claims only
    (GraphHypothesis)-[:SUBJECT|OBJECT]->(Entity); -[:PROPOSED_IN]->(Document)
    (Contradiction)-[:INVOLVES]->(GraphClaim); -[:SUPPORTED_BY]->(evidence)

Hypotheses use their own label and never get a RELATED_TO edge, so dev's
existing queries over GraphClaim/RELATED_TO cannot present them as facts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from backend.app.store.graph_repo import REFERENCE_LABELS

PIPELINE = "probabilistic-kg-v1"
SEMANTIC_LABELS = ("Entity", "GraphMention", "GraphClaim", "GraphHypothesis", "Contradiction")
CLAIM_LABELS = ("GraphClaim", "GraphHypothesis", "GraphMention", "Contradiction")
FORBIDDEN_CLAUSES = ("DELETE", "DETACH", "REMOVE", "DROP", "LOAD CSV", "CALL {", "APOC")

_TENANT = "a.tenant_id=$tenant AND b.tenant_id=$tenant"
TEMPLATES: dict[str, str] = {
    "entities": (
        "UNWIND $rows AS row MERGE (e:Entity {id: row.id}) ON CREATE SET e.created_at=timestamp() "
        "SET e.original_id=row.original_id, e.tenant_id=$tenant, e.name=row.name, "
        "e.normalized_name=row.normalized, e.kind=row.kind, e.aliases=row.aliases, "
        "e.identifiers_json=row.identifiers_json, e.description=row.description, "
        "e.entity_key=row.key, e.pipeline=$pipeline, e.updated_at=timestamp() "
        "RETURN count(e) AS written"
    ),
    "mentions": (
        "UNWIND $rows AS row MATCH (d:Document {id: row.document}) MATCH (e:Entity {id: row.entity}) "
        "WHERE d.tenant_id=$tenant AND e.tenant_id=$tenant "
        "MERGE (m:GraphMention {id: row.id}) SET m.original_id=row.original_id, m.tenant_id=$tenant, "
        "m.name=row.name, m.kind=row.kind, m.status='resolved', m.pipeline=$pipeline "
        "MERGE (m)-[:MENTIONED_IN]->(d) MERGE (m)-[:REFERS_TO]->(e) RETURN count(m) AS written"
    ),
    "claims": (
        "UNWIND $rows AS row MATCH (a:Entity {id: row.subject}) MATCH (b:Entity {id: row.object}) "
        "MATCH (d:Document {id: row.document}) WHERE " + _TENANT + " AND d.tenant_id=$tenant "
        "MERGE (c:GraphClaim {id: row.id}) SET c.original_id=row.original_id, c.tenant_id=$tenant, "
        "c.predicate=row.predicate, c.status=row.status, c.proposed_kind=row.proposed_kind, "
        "c.confidence=row.confidence, c.rationale=row.rationale, c.contested=row.contested, "
        "c.is_explicit=row.is_explicit, c.pipeline=$pipeline "
        "MERGE (c)-[:SUBJECT]->(a) MERGE (c)-[:OBJECT]->(b) MERGE (c)-[:DOCUMENTED_IN]->(d) "
        "RETURN count(c) AS written"
    ),
    "related": (
        "UNWIND $rows AS row MATCH (a:Entity {id: row.subject}) MATCH (b:Entity {id: row.object}) "
        "WHERE " + _TENANT + " MERGE (a)-[r:RELATED_TO {predicate: row.predicate}]->(b) "
        "SET r.status=row.status, r.contested=row.contested, r.pipeline=$pipeline "
        "RETURN count(r) AS written"
    ),
    "hypotheses": (
        "UNWIND $rows AS row MATCH (a:Entity {id: row.subject}) MATCH (b:Entity {id: row.object}) "
        "MATCH (d:Document {id: row.document}) WHERE " + _TENANT + " AND d.tenant_id=$tenant "
        "MERGE (h:GraphHypothesis {id: row.id}) SET h.original_id=row.original_id, "
        "h.tenant_id=$tenant, h.predicate=row.predicate, h.confidence=row.confidence, "
        "h.rationale=row.rationale, h.proposed_kind=row.proposed_kind, h.pipeline=$pipeline "
        "MERGE (h)-[:SUBJECT]->(a) MERGE (h)-[:OBJECT]->(b) MERGE (h)-[:PROPOSED_IN]->(d) "
        "RETURN count(h) AS written"
    ),
    "contradictions": (
        "UNWIND $rows AS row MERGE (x:Contradiction {id: row.id}) SET x.original_id=row.original_id, "
        "x.tenant_id=$tenant, x.description=row.description, x.source=row.source, "
        "x.pipeline=$pipeline RETURN count(x) AS written"
    ),
    "involves": (
        "UNWIND $rows AS row MATCH (a:Contradiction {id: row.contradiction}) "
        "MATCH (b:GraphClaim {id: row.claim}) WHERE " + _TENANT + " "
        "MERGE (a)-[:INVOLVES]->(b) RETURN count(*) AS written"
    ),
}
for _claim in CLAIM_LABELS:
    for _label in sorted(REFERENCE_LABELS):
        TEMPLATES[f"support:{_claim}:{_label}"] = (
            f"UNWIND $rows AS row MATCH (a:{_claim} {{id: row.claim}}) "
            f"MATCH (b:{_label} {{id: row.evidence}}) WHERE {_TENANT} "
            "MERGE (a)-[:SUPPORTED_BY]->(b) RETURN count(*) AS written"
        )
READ_TEMPLATES: dict[str, str] = {
    f"verify:{label}": (
        f"MATCH (n:{label}) WHERE n.tenant_id=$tenant AND n.id IN $ids RETURN count(n) AS present"
    )
    for label in SEMANTIC_LABELS
}
READ_TEMPLATES["export:nodes"] = (
    "MATCH (n) WHERE n.tenant_id=$tenant AND n.pipeline=$pipeline "
    "RETURN n.id AS id, labels(n) AS labels, properties(n) AS props LIMIT $limit"
)
READ_TEMPLATES["export:edges"] = (
    "MATCH (a)-[r]->(b) WHERE a.tenant_id=$tenant AND a.pipeline=$pipeline "
    "RETURN a.id AS source, type(r) AS type, b.id AS target, properties(r) AS props LIMIT $limit"
)
for _template in TEMPLATES.values():
    assert not any(clause in _template.upper() for clause in FORBIDDEN_CLAUSES), _template


@dataclass
class Statement:
    template: str
    params: dict = field(default_factory=dict)

    @property
    def query(self) -> str:
        return TEMPLATES.get(self.template) or READ_TEMPLATES[self.template]


@dataclass
class WritePlan:
    tenant: str
    statements: list[Statement]
    expected: dict[str, list[str]]  # label -> scoped IDs that must exist afterwards

    @property
    def tenant_hash(self):
        return hashlib.sha256(self.tenant.encode()).hexdigest()


def scope(tenant: str, identifier: str) -> str:
    """The same tenant scoping GraphRepository.scope applies."""
    return hashlib.sha256(tenant.encode()).hexdigest() + ":" + identifier


def entity_id(key: str) -> str:
    return "pke_" + hashlib.sha256(key.encode()).hexdigest()[:32]


def compile_plan(graph, index, tenant: str, batch_rows: int = 500) -> WritePlan:
    """Turn a ValidatedGraph into idempotent statements, batched with UNWIND."""
    entity_rows, mention_rows, claim_rows, related_rows, hypothesis_rows = [], [], [], [], []
    support: dict[tuple[str, str], list[dict]] = {}
    expected = {label: [] for label in SEMANTIC_LABELS}

    def add_support(claim_label, claim_scoped, evidence_ids):
        for evidence_id in evidence_ids:
            record = index.get(evidence_id)
            if record is None or record.label not in REFERENCE_LABELS:
                continue
            support.setdefault((claim_label, record.label), []).append(
                {"claim": claim_scoped, "evidence": scope(tenant, evidence_id)}
            )

    for key, entity in sorted(graph.entities.items()):
        original = entity_id(key)
        scoped = scope(tenant, original)
        expected["Entity"].append(scoped)
        entity_rows.append({
            "id": scoped, "original_id": original, "key": key, "name": entity.name[:240],
            "normalized": entity.name.casefold()[:240], "kind": entity.entity_type[:100],
            "aliases": [value[:240] for value in entity.aliases[:20]],
            "identifiers_json": json.dumps(entity.identifiers, sort_keys=True)[:4000],
            "description": entity.description[:600],
        })
        for document_id in entity.document_ids:
            mention = "pmention_" + hashlib.sha256(f"{document_id}:{key}".encode()).hexdigest()[:32]
            mention_scoped = scope(tenant, mention)
            expected["GraphMention"].append(mention_scoped)
            mention_rows.append({
                "id": mention_scoped, "original_id": mention, "name": entity.name[:240],
                "kind": entity.entity_type[:100], "document": scope(tenant, document_id),
                "entity": scoped,
            })
            add_support("GraphMention", mention_scoped, [
                value for value in entity.evidence_ids
                if (record := index.get(value)) is not None and record.document_id == document_id
            ])

    for claim in graph.claims:
        if claim.subject_key not in graph.entities or claim.object_key not in graph.entities:
            continue
        subject = scope(tenant, entity_id(claim.subject_key))
        obj = scope(tenant, entity_id(claim.object_key))
        scoped = scope(tenant, claim.id)
        row = {
            "id": scoped, "original_id": claim.id, "subject": subject, "object": obj,
            "document": scope(tenant, claim.document_id), "predicate": claim.predicate,
            "status": claim.status, "proposed_kind": claim.proposed_kind,
            "confidence": float(claim.confidence), "rationale": claim.rationale[:600],
            "contested": claim.contested, "is_explicit": claim.status == "observed",
        }
        if claim.verified:
            expected["GraphClaim"].append(scoped)
            claim_rows.append(row)
            related_rows.append({
                "subject": subject, "object": obj, "predicate": claim.predicate,
                "status": claim.status, "contested": claim.contested,
            })
            add_support("GraphClaim", scoped, claim.evidence_ids)
        else:
            expected["GraphHypothesis"].append(scoped)
            hypothesis_rows.append(row)
            add_support("GraphHypothesis", scoped, claim.evidence_ids)

    contradiction_rows, involves_rows = [], []
    verified_ids = {claim.id for claim in graph.claims if claim.verified}
    for contradiction in graph.contradictions:
        scoped = scope(tenant, contradiction.id)
        expected["Contradiction"].append(scoped)
        contradiction_rows.append({
            "id": scoped, "original_id": contradiction.id,
            "description": contradiction.description[:600], "source": contradiction.source,
        })
        involves_rows.extend(
            {"contradiction": scoped, "claim": scope(tenant, claim_id)}
            for claim_id in contradiction.claim_ids if claim_id in verified_ids
        )
        add_support("Contradiction", scoped, contradiction.evidence_ids)

    statements = []

    def emit(template, rows):
        for start in range(0, len(rows), batch_rows):
            statements.append(Statement(template, {"rows": rows[start:start + batch_rows]}))

    # Order matters: endpoints exist before anything that MATCHes them.
    emit("entities", entity_rows)
    emit("mentions", mention_rows)
    emit("claims", claim_rows)
    emit("related", related_rows)
    emit("hypotheses", hypothesis_rows)
    emit("contradictions", contradiction_rows)
    emit("involves", involves_rows)
    for (claim_label, evidence_label), rows in sorted(support.items()):
        emit(f"support:{claim_label}:{evidence_label}", rows)
    return WritePlan(tenant=tenant, statements=statements, expected=expected)
