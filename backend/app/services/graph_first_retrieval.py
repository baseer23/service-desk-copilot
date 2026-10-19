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
        try:
            references = lookup(
                [*plan.entities, *plan.concepts],
                plan.limit,
                document_ids=plan.document_ids,
                object_types=plan.object_types,
                relationships=plan.relationship_types,
            )
        except Exception:
            return [], "unavailable"
        if not references:
            return [], "empty"
        resolver = getattr(self.vector_store, "resolve_object_ids", None)
        if resolver is None:
            return [], "unavailable"
        query = self.embeddings.embed_texts([question])[0]
        object_ids = [row["object_id"] for row in references]
        try:
            hydrated = resolver(object_ids, query)
        except TypeError:
            # Compatibility for external vector adapters; current local stores
            # accept the query embedding and rank only hydrated objects.
            hydrated = resolver(object_ids)
        if not hydrated:
            return [], "stale"
        # Only the graph-selected objects are ranked; Neo4j never supplies content.
        return hydrated[:top_k], "available"
