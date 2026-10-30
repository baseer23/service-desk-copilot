from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List
import re

from backend.app.services.entities import extract_entities


@dataclass
class Retriever:
    """Coordinate vector, graph, and hybrid retrieval strategies."""

    settings: object
    vector_store: object
    graph_repo: object
    embedding_provider: object
    document_repository: object = None

    def keyword_search(self, question: str, top_k: int) -> List[Dict[str, object]]:
        """Explicit local excerpts, with provenance; no semantic or generated-answer claim."""
        if getattr(self.settings, "data_platform", "local") == "azure":
            return self.vector_store.search_keywords(question, top_k)
        local = self.document_repository.search(question, top_k) if self.document_repository else []
        indexed = self.vector_store.search_keywords(question, top_k)
        rows, seen = [], set()
        for row in local + indexed:
            if row["id"] not in seen:
                rows.append(row)
                seen.add(row["id"])
        return rows[:top_k]

    def vector_search(self, question: str, top_k: int) -> List[Dict[str, object]]:
        """Rank semantic chunks, preserving exact identifier hits when present."""
        embedding = self.embedding_provider.embed_texts([question])[0]
        semantic = self.vector_store.search(embedding, top_k)
        identifiers = list(
            dict.fromkeys(re.findall(r"\b[A-Za-z][A-Za-z0-9_-]*\d[A-Za-z0-9_-]*\b", question))
        )
        lookup = getattr(self.vector_store, "search_identifiers", None)
        exact = lookup(identifiers, top_k) if identifiers and lookup else []
        seen = set()
        combined = []
        for row in exact + semantic:
            if row["id"] not in seen:
                combined.append(row)
                seen.add(row["id"])
        return combined[:top_k]

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

    def hybrid_search(
        self, question: str, top_k: int, graph_plan: dict | None = None
    ) -> List[Dict[str, object]]:
        """Blend graph-anchored recall with vector ranking as a fallback."""
        from backend.app.services.graph_first_retrieval import GraphFirstRetrieval

        structured, graph_status = GraphFirstRetrieval(
            self.graph_repo, self.vector_store, self.embedding_provider
        ).search(question, top_k, graph_plan)
        if structured:
            if hasattr(self.vector_store, "hybrid"):
                embedding = self.embedding_provider.embed_texts([question])[0]
                supplement = self.vector_store.hybrid(question, embedding, top_k)
            else:
                supplement = self.vector_search(question, top_k)
            seen = set()
            combined = []
            # Graph-first: hydrated graph objects are already ranked against the
            # question, so they lead the local supplement. An exact local
            # identifier match still comes first because it names the passage.
            exact = [row for row in supplement if row.get("match_type") == "exact_identifier"]
            for row in exact + structured + supplement:
                if row["id"] not in seen:
                    combined.append(row)
                    seen.add(row["id"])
            for row in combined:
                row.setdefault("graph_enrichment", graph_status)
            return combined[:top_k]
        if hasattr(self.vector_store, "hybrid"):
            embedding = self.embedding_provider.embed_texts([question])[0]
            local = self.vector_store.hybrid(question, embedding, top_k)
            graph = self.graph_search(question, top_k)
            seen, rows = set(), []
            # Keep exact identifiers and local lexical/semantic ranking, while
            # allowing genuine graph-linked chunks to extend recall.
            for row in local + graph:
                if row["id"] not in seen:
                    rows.append(row)
                    seen.add(row["id"])
            for row in rows:
                row.setdefault("graph_enrichment", graph_status)
            return rows[:top_k]
        graph_results = self.graph_search(question, top_k)
        if not graph_results:
            return self.vector_search(question, top_k)

        allowed_ids = {item["id"] for item in graph_results}
        vector_results = self.vector_search(question, top_k)
        filtered = [item for item in vector_results if item["id"] in allowed_ids]
        exact = [item for item in vector_results if item.get("match_type") == "exact_identifier"]
        if exact:
            # Broad graph entities must not eliminate an exact reported code.
            exact_ids = {item["id"] for item in exact}
            return (exact + [item for item in vector_results if item["id"] not in exact_ids])[
                :top_k
            ]
        return filtered or vector_results
