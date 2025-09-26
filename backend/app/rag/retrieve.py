from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from backend.app.services.entities import extract_entities


@dataclass
class Retriever:
    """Coordinate vector, graph, and hybrid retrieval strategies."""
    settings: object
    vector_store: object
    graph_repo: object
    embedding_provider: object

    def vector_search(self, question: str, top_k: int) -> List[Dict[str, object]]:
        """Return top-k chunks ranked purely by vector similarity."""
        embedding = self.embedding_provider.embed_texts([question])[0]
        return self.vector_store.search(embedding, top_k)

    def graph_search(self, question: str, top_k: int) -> List[Dict[str, object]]:
        """Fetch chunks linked to question entities from the graph store."""
        entities = extract_entities([{"text": question}])
        if not entities:
            return []
        degrees = self.graph_repo.get_entity_degrees(entities)
        relevant = [name for name, degree in degrees.items() if degree > 0]
        if not relevant:
            return []
        return self.graph_repo.fetch_chunks_for_entities(relevant, top_k)

    def hybrid_search(self, question: str, top_k: int) -> List[Dict[str, object]]:
        """Blend graph-anchored recall with vector ranking as a fallback."""
        graph_results = self.graph_search(question, top_k)
        if not graph_results:
            return self.vector_search(question, top_k)

        allowed_ids = {item["id"] for item in graph_results}
        vector_results = self.vector_search(question, top_k)
        filtered = [item for item in vector_results if item["id"] in allowed_ids]
        return filtered or vector_results
