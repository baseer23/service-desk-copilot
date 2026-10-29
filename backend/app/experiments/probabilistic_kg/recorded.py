"""Turn the recorded model readings into each approach's input.

B and C receive ``ProbabilisticGraphProposal`` objects citing element-level
evidence (paragraphs, table cells, form fields). Approach A receives the same
reading as dev's ``DocumentGraphProposal``, at the granularity dev's
understanding step accepts (chunks, tables, sections, figures, form fields,
from ``split_structured``); A has no observed/derived/hypothesis label and
cannot reference another document's entity, so those parts are recorded as
not representable rather than silently dropped.
"""

from __future__ import annotations

from backend.app.experiments.probabilistic_kg.fixtures import MODEL_READINGS
from backend.app.experiments.probabilistic_kg.models import (
    EXISTING_PREFIX,
    ProbabilisticGraphProposal,
)
from backend.app.models.document_graph_models import (
    DocumentGraphProposal,
    EntityPlacement,
    GraphPlacementPlan,
)
from backend.app.services.form_marks import readable_marks


def resolve_ref(index, ref):
    """Resolve a fixture evidence reference to an evidence ID (or None)."""
    kind, _, value = ref.partition(":")
    for record in index.records.values():
        if kind == "p" and record.label == "Paragraph" and record.source_element_id == value:
            return record.evidence_id
        if kind == "f" and record.label == "FormField":
            document, _, prefix = value.partition(":")
            if record.document_id == document and str(record.extra.get("question", "")).startswith(
                prefix
            ):
                return record.evidence_id
        if kind == "c" and record.label == "TableCell":
            table, row, column = value.split(":")
            extra = record.extra
            if (extra.get("table_id"), extra.get("row"), extra.get("column")) == (
                table, int(row), int(column)
            ):
                return record.evidence_id
        if kind == "t" and record.label == "Table" and record.extra.get("table_id") == value:
            return record.evidence_id
    return None


def container(index, evidence_id):
    """The object dev's understanding step would cite for an element."""
    record = index.get(evidence_id)
    if record is None or record.label in {"FormField", "Table", "Chunk", "Section", "Figure"}:
        return evidence_id if record else None
    if record.label == "TableCell":
        for other in index.for_document(record.document_id):
            if other.label == "Table" and other.extra.get("table_id") == record.extra.get(
                "table_id"
            ):
                return other.evidence_id
        return None
    text = readable_marks(record.text).strip()
    for other in index.for_document(record.document_id):
        if other.label == "Chunk" and other.page == record.page and text and text in readable_marks(
            other.text
        ):
            return other.evidence_id
    return None


def proposal_b(index, document_id):
    reading = MODEL_READINGS[document_id]

    def ids(refs):
        return [value for ref in refs if (value := resolve_ref(index, ref))]

    return ProbabilisticGraphProposal.model_validate({
        "document_id": document_id,
        "document_type": reading.get("document_type", "Document"),
        "concepts": reading.get("concepts", []),
        "entities": [
            {"id": e["id"], "name": e["name"], "entity_type": e["type"],
             "aliases": e.get("aliases", []), "identifiers": e.get("identifiers", {}),
             "evidence_ids": ids(e["evidence"]), "confidence": 0.9}
            for e in reading.get("entities", [])
        ],
        "relationships": [
            {"subject_id": r["s"], "predicate": r["p"], "object_id": r["o"], "kind": r["kind"],
             "evidence_ids": ids(r["evidence"]), "confidence": r.get("confidence", 0.5)}
            for r in reading.get("relationships", [])
        ],
        "form_readings": [
            {"field_evidence_id": resolve_ref(index, f["field"]) or f["field"],
             "selected_option": f["selected_option"]}
            for f in reading.get("form_readings", [])
        ],
        "contradictions": [
            {"description": c["description"], "evidence_ids": ids(c["evidence"])}
            for c in reading.get("contradictions", [])
        ],
    })


def proposal_a(index, document_id):
    """The same reading in dev's schema; returns (proposal, not_representable)."""
    reading = MODEL_READINGS[document_id]
    not_representable = []

    def ids(refs):
        values = [container(index, resolve_ref(index, ref)) for ref in refs]
        return list(dict.fromkeys(value for value in values if value))

    entities = [
        {"temporary_id": e["id"], "name": e["name"], "entity_type": e["type"],
         "aliases": e.get("aliases", []), "identifiers": e.get("identifiers", {}),
         "evidence_object_ids": ids(e["evidence"])}
        for e in reading.get("entities", [])
    ]
    relationships = []
    for r in reading.get("relationships", []):
        if r["s"].startswith(EXISTING_PREFIX) or r["o"].startswith(EXISTING_PREFIX):
            not_representable.append({"document_id": document_id, "predicate": r["p"],
                                      "reason": "references another document's entity"})
            continue
        relationships.append({
            "source_temporary_id": r["s"], "target_temporary_id": r["o"], "predicate": r["p"],
            "evidence_object_ids": ids(r["evidence"]), "confidence": r.get("confidence", 0.5),
            "is_explicit": r["kind"] == "observed",
            # dev's schema now carries the claim status the model reported.
            "status": r["kind"],
        })
    for c in reading.get("contradictions", []):
        not_representable.append({"document_id": document_id, "item": c["description"],
                                  "reason": "dev's schema has no contradiction field"})
    if reading.get("form_readings"):
        not_representable.append({"document_id": document_id,
                                  "reason": "dev's schema has no form-reading field"})
    return DocumentGraphProposal.model_validate({
        "document_id": document_id, "document_type": reading.get("document_type", "Document"),
        "summary": ", ".join(reading.get("concepts", [])), "entities": entities,
        "relationships": relationships,
    }), not_representable


class BaselineRecordedProvider:
    """Stands in for the model in dev's understanding and placement steps.

    Section calls return the part of the recorded reading that cites objects
    in that batch; the consolidation call returns the whole reading; placement
    reuses a discovered candidate when discovery found one (discovery itself
    only matches names, aliases and identifiers) and otherwise creates a new
    entity. It is faithful to the reading, never better than it.
    """

    def __init__(self, index):
        self.index = index
        self.calls = []

    def name(self):
        return "recorded"

    async def generate_structured(self, stage, context, schema):
        self.calls.append(stage)
        if stage == "graph_placement":
            placements = []
            for temporary_id in context["required_temporary_ids"]:
                candidates = context["allowed_candidate_ids"].get(temporary_id, [])
                placements.append(
                    EntityPlacement(temporary_id=temporary_id, decision="REUSE_EXISTING",
                                    existing_entity_id=candidates[0],
                                    reason="Discovery matched its name, alias or identifier.")
                    if candidates else
                    EntityPlacement(temporary_id=temporary_id, decision="CREATE_NEW",
                                    reason="No existing candidate.")
                )
            return GraphPlacementPlan(document_id=context["required_document_id"],
                                      placements=placements)
        if "section_analyses" in context:
            proposal, _ = proposal_a(self.index, context["document_id"])
            return proposal
        document_id = context["document"]["id"]
        batch = {item["object_id"] for item in context["objects"]}
        full, _ = proposal_a(self.index, document_id)
        data = full.model_dump()
        for entity in data["entities"]:
            entity["evidence_object_ids"] = [v for v in entity["evidence_object_ids"] if v in batch]
        data["entities"] = [e for e in data["entities"] if e["evidence_object_ids"]]
        kept = {e["temporary_id"] for e in data["entities"]}
        for relation in data["relationships"]:
            relation["evidence_object_ids"] = [
                v for v in relation["evidence_object_ids"] if v in batch
            ]
        data["relationships"] = [
            r for r in data["relationships"]
            if r["source_temporary_id"] in kept and r["target_temporary_id"] in kept
            and r["evidence_object_ids"]
        ]
        return DocumentGraphProposal.model_validate(data)
