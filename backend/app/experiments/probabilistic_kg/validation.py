"""Deterministic validation between model proposals and the graph.

The model proposes; this module decides what may be written and with which
status. It never upgrades a claim: high confidence cannot turn a hypothesis
into a fact. What it can check:

* every cited evidence ID exists in that document;
* an entity's name (or an alias) appears in at least one cited element;
* an identifier is used for cross-document identity only if it appears in
  the cited evidence;
* an "observed" claim has one cited element mentioning both endpoints,
  otherwise it is demoted to "derived" (both endpoints grounded in different
  cited elements) or "hypothesis";
* a claim about a form answer matches the extracted check-box selection;
* claims with the same subject and a single-valued predicate but different
  objects are marked contested.

It does not verify that the predicate itself is what the text says; that is a
stated limitation (see the report).
"""

from __future__ import annotations

import hashlib
import re

from backend.app.experiments.probabilistic_kg.models import (
    EXISTING_PREFIX,
    ProbabilisticGraphProposal,
    Rejection,
    ValidatedClaim,
    ValidatedContradiction,
    ValidatedEntity,
    ValidatedGraph,
)
from backend.app.services.grounding import mentions, normalize

# Predicates whose subject can hold only one value at a time. A second value
# from different evidence is a contradiction to surface, not a fact to choose.
SINGLE_VALUED_PREDICATES = {
    "EFFECTIVE_DATE", "START_DATE", "END_DATE", "EXPIRY_DATE", "TERMINATION_DATE",
    "EMPLOYMENT_STATUS", "NOTICE_PERIOD", "PREMIUM_AMOUNT", "TOTAL_AMOUNT", "DUE_DATE",
    "HEADQUARTERED_IN", "PAYMENT_TERMS",
}
# Without a grounded identifier, these names are not unique across documents.
DOCUMENT_SCOPED_TYPES = {"person", "date", "amount", "duration", "value"}


def entity_key(entity_type: str, name: str, identifiers: dict, document_id: str) -> str:
    kind = normalize(entity_type).replace(" ", "_")
    if identifiers:
        scheme, value = sorted(identifiers.items())[0]
        return f"{kind}:id:{normalize(scheme)}={normalize(value)}"
    if kind in DOCUMENT_SCOPED_TYPES:
        return f"{kind}:doc:{document_id}:{normalize(name)}"
    return f"{kind}:name:{normalize(name)}"


def _stable(*parts) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:32]


class ProposalValidator:
    def __init__(self, evidence_index):
        self.index = evidence_index

    def validate(self, proposals, existing: ValidatedGraph | None = None) -> ValidatedGraph:
        graph = existing.model_copy(deep=True) if existing else ValidatedGraph()
        for proposal in proposals:
            self._validate_document(graph, proposal)
        self._detect_contradictions(graph)
        return graph

    # -- per document -----------------------------------------------------
    def _evidence(self, document_id, ids):
        known = []
        for value in ids:
            record = self.index.get(value)
            if record is not None and record.document_id == document_id:
                known.append(record)
        return known

    def _reject(self, graph, document_id, item_type, item, reason):
        graph.rejections.append(Rejection(
            document_id=document_id, item_type=item_type, item=item, reason=reason,
        ))

    def _validate_document(self, graph, proposal: ProbabilisticGraphProposal):
        document_id = proposal.document_id
        accepted = {}  # proposal entity id -> (key, names, evidence records)
        for entity in proposal.entities:
            evidence = self._evidence(document_id, entity.evidence_ids)
            if not evidence:
                self._reject(graph, document_id, "entity", entity.model_dump(), "unknown_evidence")
                continue
            names = [entity.name, *entity.aliases]
            grounded = [record for record in evidence if mentions(record.text, names)]
            if not grounded:
                self._reject(graph, document_id, "entity", entity.model_dump(),
                             "name_not_in_cited_evidence")
                continue
            identifiers = {
                scheme: value for scheme, value in entity.identifiers.items()
                if any(mentions(record.text, [value]) for record in evidence)
            }
            if len(identifiers) != len(entity.identifiers):
                graph.demotions.append({
                    "document_id": document_id, "entity": entity.name,
                    "reason": "identifier_not_in_cited_evidence",
                })
            key = self._resolve(graph, entity, identifiers, document_id)
            current = graph.entities.get(key)
            if current is not None:
                current.identifiers = {**identifiers, **current.identifiers}
            if current is None:
                graph.entities[key] = ValidatedEntity(
                    key=key, name=entity.name, entity_type=entity.entity_type,
                    aliases=entity.aliases, identifiers=identifiers,
                    description=entity.description, document_ids=[document_id],
                    evidence_ids=[record.evidence_id for record in grounded],
                )
            else:
                current.aliases = list(dict.fromkeys(
                    [*current.aliases, *entity.aliases,
                     *([entity.name] if entity.name != current.name else [])]
                ))[:20]
                current.document_ids = list(dict.fromkeys([*current.document_ids, document_id]))
                current.evidence_ids = list(dict.fromkeys(
                    [*current.evidence_ids, *(record.evidence_id for record in grounded)]
                ))[:100]
            accepted[entity.id] = (key, names, evidence)

        for link in proposal.entity_links:
            if link.entity_id not in accepted:
                self._reject(graph, document_id, "entity_link", link.model_dump(),
                             "entity_rejected")
                continue
            key = accepted[link.entity_id][0]
            target = graph.entities.get(link.existing_entity_key)
            if target is None:
                self._reject(graph, document_id, "entity_link", link.model_dump(),
                             "unknown_existing_entity")
                continue
            source = graph.entities[key]
            same_identifier = bool(set(source.identifiers.items()) & set(target.identifiers.items()))
            same_name = normalize(source.name) in {
                normalize(value) for value in [target.name, *target.aliases]
            }
            if not (same_identifier or same_name):
                # Similar wording alone never merges two identities.
                self._reject(graph, document_id, "entity_link", link.model_dump(),
                             "identity_not_established")
                continue
            if key != target.key:
                target.aliases = list(dict.fromkeys([*target.aliases, source.name]))[:20]
                target.document_ids = list(dict.fromkeys([*target.document_ids, *source.document_ids]))
                target.evidence_ids = list(dict.fromkeys([*target.evidence_ids, *source.evidence_ids]))
                del graph.entities[key]
                accepted[link.entity_id] = (target.key, *accepted[link.entity_id][1:])

        fields = {record.evidence_id: record for record in self.index.form_fields(document_id)}
        options = {
            normalize(option) for record in fields.values()
            for option in record.extra.get("options", [])
        }
        for relation in proposal.relationships:
            item = relation.model_dump()
            endpoints = [self._endpoint(graph, accepted, value)
                         for value in (relation.subject_id, relation.object_id)]
            if None in endpoints:
                self._reject(graph, document_id, "relationship", item, "endpoint_rejected")
                continue
            # A relationship may join evidence across documents (for example a
            # form answer and a policy clause) but must cite its own document.
            evidence = [record for value in relation.evidence_ids
                        if (record := self.index.get(value)) is not None]
            if not any(record.document_id == document_id for record in evidence):
                self._reject(graph, document_id, "relationship", item, "unknown_evidence")
                continue
            (subject_key, subject_names), (object_key, object_names) = endpoints
            form_issue = self._form_issue(evidence, fields, options, subject_names, object_names)
            if form_issue:
                self._reject(graph, document_id, "relationship", item, form_issue)
                continue
            both = [
                record for record in evidence
                if (record.label == "FormField" or mentions(record.text, subject_names))
                and mentions(record.text, object_names)
            ]
            subject_cover = [
                record for record in evidence
                if record.label == "FormField" or mentions(record.text, subject_names)
            ]
            object_cover = [record for record in evidence if mentions(record.text, object_names)]
            status, reason = relation.kind, None
            if relation.kind == "observed" and not both:
                if subject_cover and object_cover:
                    status, reason = "derived", "observed_needs_one_element_with_both_endpoints"
                else:
                    status, reason = "hypothesis", "endpoint_not_in_cited_evidence"
            elif relation.kind == "derived" and not (subject_cover and object_cover):
                status, reason = "hypothesis", "endpoint_not_in_cited_evidence"
            if reason:
                graph.demotions.append({
                    "document_id": document_id, "predicate": relation.predicate,
                    "from": relation.kind, "to": status, "reason": reason,
                })
            claim_id = "pclaim_" + _stable(subject_key, relation.predicate, object_key, document_id)
            if any(claim.id == claim_id for claim in graph.claims):
                continue
            graph.claims.append(ValidatedClaim(
                id=claim_id, subject_key=subject_key, predicate=relation.predicate,
                object_key=object_key, status=status, proposed_kind=relation.kind,
                document_id=document_id,
                evidence_ids=[record.evidence_id for record in evidence],
                confidence=relation.confidence, rationale=relation.rationale,
            ))

        for reading in proposal.form_readings:
            graph.form_readings_checked += 1
            record = fields.get(reading.field_evidence_id)
            actual = record.extra.get("selected_option") if record else None
            status = record.extra.get("field_status") if record else None
            proposed = normalize(reading.selected_option) if reading.selected_option else None
            if record is None or (status == "answered" and proposed != normalize(actual)) or (
                status != "answered" and proposed is not None
            ):
                graph.form_readings_rejected += 1
                self._reject(graph, document_id, "form_reading", {
                    **reading.model_dump(), "extracted_selection": actual,
                    "extracted_status": status,
                }, "contradicts_extracted_selection")

        for contradiction in proposal.contradictions:
            evidence = self._evidence(document_id, contradiction.evidence_ids)
            evidence += [
                record for value in contradiction.evidence_ids
                if (record := self.index.get(value)) is not None and record.document_id != document_id
            ]
            if len(evidence) < 2:
                self._reject(graph, document_id, "contradiction", contradiction.model_dump(),
                             "needs_two_cited_elements")
                continue
            ids = sorted({record.evidence_id for record in evidence})
            graph.contradictions.append(ValidatedContradiction(
                id="contra_" + _stable(*ids), description=contradiction.description,
                evidence_ids=ids, source="model",
            ))

    @staticmethod
    def _endpoint(graph, accepted, value):
        if value.startswith(EXISTING_PREFIX):
            entity = graph.entities.get(value[len(EXISTING_PREFIX):])
            return (entity.key, [entity.name, *entity.aliases]) if entity else None
        if value in accepted:
            key, names, _evidence = accepted[value]
            return key, names
        return None

    @staticmethod
    def _resolve(graph, entity, identifiers, document_id):
        """Reuse an entity with the same grounded identifier, or the same exact
        name and type for kinds whose names identify them; otherwise a new key."""
        kind = normalize(entity.entity_type)
        for key, existing in graph.entities.items():
            if normalize(existing.entity_type) == kind and set(identifiers.items()) & set(
                existing.identifiers.items()
            ):
                return key
        if kind.replace(" ", "_") not in DOCUMENT_SCOPED_TYPES:
            names = {normalize(entity.name), *(normalize(alias) for alias in entity.aliases)}
            for key, existing in graph.entities.items():
                if normalize(existing.entity_type) == kind and names & {
                    normalize(existing.name), *(normalize(alias) for alias in existing.aliases)
                }:
                    return key
        return entity_key(entity.entity_type, entity.name, identifiers, document_id)

    @staticmethod
    def _form_issue(evidence, fields, options, subject_names, object_names):
        """A claim that states a form answer must match the extracted selection."""
        cited = [fields[record.evidence_id] for record in evidence if record.evidence_id in fields]
        values = {normalize(name) for name in [*subject_names, *object_names]} & options
        if not cited and not values:
            return None
        answered = [
            field for field in cited if field.extra.get("field_status") == "answered"
        ]
        if not answered:
            return "form_value_without_answered_field"
        selected = {normalize(field.extra.get("selected_option")) for field in answered}
        if values and not values & selected:
            return "contradicts_extracted_selection"
        return None

    # -- across documents ---------------------------------------------------
    @staticmethod
    def _detect_contradictions(graph):
        groups = {}
        for claim in graph.claims:
            if claim.verified and claim.predicate in SINGLE_VALUED_PREDICATES:
                groups.setdefault((claim.subject_key, claim.predicate), []).append(claim)
        for (_subject, predicate), claims in groups.items():
            if len({claim.object_key for claim in claims}) < 2:
                continue
            for claim in claims:
                claim.contested = True
            ids = sorted({value for claim in claims for value in claim.evidence_ids})
            claim_ids = sorted(claim.id for claim in claims)
            graph.contradictions.append(ValidatedContradiction(
                id="contra_" + _stable(*claim_ids),
                description=f"Conflicting values for {predicate}",
                claim_ids=claim_ids, evidence_ids=ids, source="deterministic",
            ))
        # A model-reported contradiction contests only claims that actually
        # disagree: same subject and predicate, different objects, each side
        # citing one of the contradiction's evidence elements.
        for contradiction in graph.contradictions:
            if contradiction.source != "model":
                continue
            cited = set(contradiction.evidence_ids)
            groups = {}
            for claim in graph.claims:
                if claim.verified and cited & set(claim.evidence_ids):
                    groups.setdefault((claim.subject_key, claim.predicate), []).append(claim)
            for claims in groups.values():
                if len({claim.object_key for claim in claims}) < 2:
                    continue
                for claim in claims:
                    claim.contested = True
                    contradiction.claim_ids = sorted({*contradiction.claim_ids, claim.id})
