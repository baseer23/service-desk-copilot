"""Bounded graph-reference planning and tenant-safe local hydration."""

from backend.app.agent.models import GraphLookupPlan
from backend.app.services.entities import extract_entities


class GraphFirstRetrieval:
    def __init__(self, graph_repo, vector_store, embeddings):
        self.graph_repo = graph_repo
        self.vector_store = vector_store
        self.embeddings = embeddings

    def plan(self, question: str) -> GraphLookupPlan:
        entities = extract_entities([{"text": question}])[:10]
        concepts = list(dict.fromkeys([*entities, *question.split()]))[:10]
        return GraphLookupPlan(
            entities=entities,
            concepts=concepts,
            relationship_types=[
                "HAS_OBJECT",
                "HAS_SECTION",
                "HAS_CHUNK",
                "HAS_TABLE",
                "HAS_FIGURE",
                "REFERENCES_TABLE",
                "REFERENCES_FIGURE",
                "MENTIONS",
                "RELATED_TO",
            ],
            limit=20,
        )

    def search(self, question: str, top_k: int, graph_plan: dict | None = None):
        """Read references first, then hydrate/rank only local authorized content."""
        lookup = getattr(self.graph_repo, "fetch_structured_references", None)
        if not getattr(self.graph_repo, "available", True) or lookup is None:
            return [], "unavailable"
        plan = GraphLookupPlan.model_validate(graph_plan) if graph_plan else self.plan(question)
        references = lookup(
            [*plan.entities, *plan.concepts],
            plan.limit,
            document_ids=plan.document_ids,
            object_types=plan.object_types,
            relationships=plan.relationship_types,
        )
        if not references:
            return [], "empty"
        resolver = getattr(self.vector_store, "resolve_object_ids", None)
        if resolver is None:
            return [], "unavailable"
        hydrated = resolver([row["object_id"] for row in references])
        if not hydrated:
            return [], "stale"
        query = self.embeddings.embed_texts([question])[0]
        # Chroma remains authoritative for semantic ranking. Restrict its ranked
        # output to hydrated IDs so Aura never supplies content.
        allowed = {row["id"] for row in hydrated}
        ranked = [
            row
            for row in self.vector_store.search(query, max(top_k * 4, 20))
            if row["id"] in allowed
        ]
        by_id = {row["id"]: row for row in ranked}
        return [by_id.get(row["id"], row) for row in hydrated][:top_k], "available"
