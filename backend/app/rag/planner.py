from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from backend.app.services.entities import extract_entities

GRAPH_THRESHOLD = 3


@dataclass
class Planner:
    settings: object
    graph_repo: object

    def plan(self, question: str) -> Dict[str, object]:
        entities = extract_entities([{"text": question}])
        entity_degrees = self.graph_repo.get_entity_degrees(entities) if entities else {}
        top_k = getattr(self.settings, "top_k", 6)

        if not entity_degrees or all(degree == 0 for degree in entity_degrees.values()):
            return {
                "mode": "VECTOR",
                "reasons": ["No relevant entities detected"],
                "top_k": top_k,
                "entities": [],
            }

        max_degree = max(entity_degrees.values())
        normalized_entities = [name for name, degree in entity_degrees.items() if degree > 0]

        if max_degree >= GRAPH_THRESHOLD:
            mode = "GRAPH"
            reasons = [f"High degree entity detected ({max_degree})"]
        else:
            mode = "HYBRID"
            reasons = ["Entities present but graph is sparse"]

        return {
            "mode": mode,
            "reasons": reasons,
            "top_k": top_k,
            "entities": normalized_entities,
        }
