"""Conservative model-assisted placement with trusted graph writes."""

import hashlib

from backend.app.models.document_graph_models import GraphPlacementPlan
from backend.app.services.graph_discovery import GraphDiscovery


class GraphPlacementService:
    def __init__(self, provider, graph, vector_store, records):
        self.provider, self.graph, self.vector_store, self.records = provider, graph, vector_store, records
        self._proposal = None
        self._discovery = None

    async def prepare_placement(self, proposal, discovery=None):
        evidence_ids = list(dict.fromkeys(
            object_id
            for item in [*proposal.entities, *proposal.relationships]
            for object_id in item.evidence_object_ids
        ))
        hydrated = self.vector_store.resolve_object_ids(evidence_ids)
        available = {row.get("metadata", {}).get("object_id") for row in hydrated}
        if any(object_id not in available for object_id in evidence_ids):
            raise ValueError("Proposal contains unavailable supporting object references")
        discovery = discovery or GraphDiscovery(self.graph).discover(proposal)
        context = {
            "policy": "Choose exactly one conservative decision per proposed entity. Reuse only strong identifiers or consistent graph context. Similar spelling alone is insufficient. Use only candidate IDs supplied here.",
            "proposal": proposal.model_dump(), "candidates": discovery,
        }
        plan = await self.provider.generate_structured("graph_placement", context, GraphPlacementPlan)
        self._validate(proposal, plan, discovery)
        self._proposal, self._discovery = proposal, discovery
        return plan

    def _validate(self, proposal, plan, discovery):
        proposed = {item.temporary_id for item in proposal.entities}
        if (plan.document_id != proposal.document_id or len(plan.placements) != len(proposed)
                or {p.temporary_id for p in plan.placements} != proposed):
            raise ValueError("Placement must decide every proposed entity exactly once")
        for placement in plan.placements:
            allowed = {row["id"] for row in discovery[placement.temporary_id]["candidates"]}
            if placement.existing_entity_id and placement.existing_entity_id not in allowed:
                raise ValueError("Placement referenced an unsupported existing entity")

    def apply_placement(self, plan, proposal=None):
        proposal = proposal or self._proposal
        if proposal is None:
            raise ValueError("A validated proposal is required")
        entities = {item.temporary_id: item for item in proposal.entities}
        reused = [item.existing_entity_id for item in plan.placements if item.existing_entity_id]
        if set(reused) != self.graph.existing_entity_ids(reused):
            raise ValueError("A reused entity is no longer available in the current tenant")
        mapping, writes, mentions = {}, [], []
        for placement in plan.placements:
            entity = entities[placement.temporary_id]
            if placement.decision == "REUSE_EXISTING":
                entity_id = placement.existing_entity_id
            elif placement.decision == "CREATE_NEW":
                identity = f"{proposal.document_id}:{entity.temporary_id}:{entity.entity_type}:{entity.name}"
                entity_id = "ke_" + hashlib.sha256(identity.encode()).hexdigest()[:32]
                writes.append({"id": entity_id, "name": entity.name, "kind": entity.entity_type,
                    "aliases": entity.aliases, "identifiers": entity.identifiers,
                    "description": entity.description})
            else:
                entity_id = None
            mapping[entity.temporary_id] = entity_id
            mentions.append({"mention_id": entity.temporary_id, "entity_id": entity_id,
                "name": entity.name, "kind": entity.entity_type,
                "evidence_object_ids": entity.evidence_object_ids,
                "status": "resolved" if entity_id else "unresolved"})
        self.graph.upsert_knowledge_entities(writes)
        relationships = []
        for relation in proposal.relationships:
            source, target = mapping.get(relation.source_temporary_id), mapping.get(relation.target_temporary_id)
            if not source or not target or source == target:
                continue
            relationships.append({**relation.model_dump(), "source_id": source, "target_id": target,
                                  "document_id": proposal.document_id})
        self.graph.upsert_knowledge_relationships(relationships)
        self.graph.link_document_mentions(proposal.document_id, mentions)
        return {"entities_created": len(writes), "mentions": len(mentions),
                "relationships": len(relationships), "unresolved": sum(not v for v in mapping.values())}
