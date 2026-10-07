from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.app.models.dto import AskResponse
from backend.app.mcp.client import DeskMateMCPClient
from backend.app.rag.answer import Responder
from backend.app.services.ingest_service import IngestService

logger = logging.getLogger("service-desk")


class ResearchState(TypedDict):
    """Minimal state carried by the LangGraph research loop."""

    question: str
    top_k: int
    local_context: List[Dict[str, Any]]
    web_results: List[Dict[str, Any]]
    evidence: Dict[str, Any]
    iterations: int
    planner: Dict[str, Any]
    provider: Any
    response: AskResponse | None


class ResearchGraph:
    """LangGraph workflow that keeps local retrieval and web fallback in a strict loop."""

    def __init__(self, *, settings: Any, vector_store: Any, graph_repo: Any, embedding_provider: Any, provider: Any) -> None:
        self.settings = settings
        self.vector_store = vector_store
        self.graph_repo = graph_repo
        self.embedding_provider = embedding_provider
        self.provider = provider
        self.mcp = DeskMateMCPClient()

    def build_graph(self) -> StateGraph:
        graph = StateGraph(ResearchState)

        graph.add_node("retrieve_local", self._retrieve_local)
        graph.add_node("evaluate_evidence", self._evaluate_evidence)
        graph.add_node("search_web", self._search_web)
        graph.add_node("fetch_and_ingest", self._fetch_and_ingest)
        graph.add_node("finalize", self._finalize)

        graph.add_edge(START, "retrieve_local")
        graph.add_conditional_edges(
            "retrieve_local",
            self._after_local_retrieval,
            {
                "evaluate": "evaluate_evidence",
                "finalize": "finalize",
            },
        )
        graph.add_conditional_edges(
            "evaluate_evidence",
            self._after_evaluation,
            {
                "search": "search_web",
                "finalize": "finalize",
            },
        )
        graph.add_edge("search_web", "fetch_and_ingest")
        graph.add_conditional_edges(
            "fetch_and_ingest",
            self._after_fetch,
            {
                "search": "search_web",
                "finalize": "finalize",
            },
        )
        graph.add_edge("finalize", END)
        return graph

    def run(self, question: str, *, top_k: int = 6, max_iterations: int = 3) -> AskResponse:
        graph = self.build_graph()
        app = graph.compile()
        result = app.invoke(
            {
                "question": question,
                "top_k": top_k,
                "local_context": [],
                "web_results": [],
                "evidence": {"needs_web": False, "score": 0.0},
                "iterations": 0,
                "planner": {"mode": "HYBRID", "research_loop": True},
                "provider": self.provider,
                "response": None,
            }
        )
        response = result.get("response")
        if response is None:
            responder = Responder(settings=self.settings, provider=self.provider)
            response = responder.answer(question, result.get("planner", {"mode": "HYBRID"}), result.get("local_context", []))
        return response

    def _retrieve_local(self, state: ResearchState) -> Dict[str, Any]:
        try:
            local_context = self.mcp.search_knowledge(state["question"], "HYBRID", state["top_k"])
        except Exception as exc:  # pragma: no cover - protocol failure
            logger.warning("Local MCP retrieval failed during research loop: %s", exc)
            local_context = []
        evidence = self._score_evidence(local_context)
        return {"local_context": local_context, "evidence": evidence}

    def _evaluate_evidence(self, state: ResearchState) -> Dict[str, Any]:
        evidence = self._score_evidence(state["local_context"])
        return {"evidence": evidence}

    def _search_web(self, state: ResearchState) -> Dict[str, Any]:
        question = state["question"]
        try:
            search_results = self.mcp.search_web(question, max_results=5)
        except Exception as exc:  # pragma: no cover - network-dependent
            logger.warning("Web search failed in research loop: %s", exc)
            search_results = []
        iterations = int(state.get("iterations", 0)) + 1
        return {"web_results": search_results, "iterations": iterations}

    def _fetch_and_ingest(self, state: ResearchState) -> Dict[str, Any]:
        merged_context = list(state.get("local_context", []))
        for result in state.get("web_results", [])[:2]:
            url = result.get("url") or ""
            if not url:
                continue
            try:
                page = self.mcp.fetch_web_page(url)
                text = (page or {}).get("text") or ""
                title = (page or {}).get("title") or result.get("title") or url
                if not text.strip():
                    continue
                service = IngestService(
                    settings=self.settings,
                    vector_store=self.vector_store,
                    graph_repo=self.graph_repo,
                    embedding_provider=self.embedding_provider,
                )
                payload = service.ingest_text(title, text)
                if payload.chunks > 0:
                    try:
                        refresh = self.mcp.search_knowledge(state["question"], "HYBRID", state["top_k"])
                        merged_context = self._merge_contexts(merged_context, refresh)
                    except Exception as exc:  # pragma: no cover - runtime dependent
                        logger.warning("Refresh after web ingestion failed: %s", exc)
            except Exception as exc:  # pragma: no cover - network dependent
                logger.warning("Fetch-and-ingest for %s failed in research loop: %s", url, exc)
        evidence = self._score_evidence(merged_context)
        return {"local_context": merged_context, "evidence": evidence}

    def _finalize(self, state: ResearchState) -> Dict[str, Any]:
        question = state["question"]
        responder = Responder(settings=self.settings, provider=self.provider)
        response = responder.answer(question, state.get("planner", {"mode": "HYBRID"}), state.get("local_context", []))
        evidence = self._score_evidence(state.get("local_context", []))
        response.confidence = self._confidence_from_evidence(evidence)
        if evidence.get("needs_web") and state.get("web_results"):
            response.answer = f"{response.answer}\n\n[low-confidence: local evidence was sparse, and public web fallback did not add enough support]"
        return {"response": response}

    def _after_local_retrieval(self, state: ResearchState) -> str:
        if state.get("local_context"):
            return "evaluate"
        return "finalize"

    def _after_evaluation(self, state: ResearchState) -> str:
        evidence = state.get("evidence", {})
        if evidence.get("needs_web") and int(state.get("iterations", 0)) < 3:
            return "search"
        return "finalize"

    def _after_fetch(self, state: ResearchState) -> str:
        evidence = self._score_evidence(state.get("local_context", []))
        if evidence.get("needs_web") and int(state.get("iterations", 0)) < 3:
            return "search"
        return "finalize"

    def _score_evidence(self, chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not chunks:
            return {"has_evidence": False, "needs_web": True, "score": 0.0}
        scores = [float(chunk.get("score", 0.0) or 0.0) for chunk in chunks]
        mean_score = sum(scores) / len(scores)
        has_evidence = len(chunks) > 0 and mean_score < 1.0
        return {"has_evidence": has_evidence, "needs_web": not has_evidence, "score": mean_score}

    def _merge_contexts(self, base: List[Dict[str, Any]], extra: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        for item in [*base, *extra]:
            chunk_id = item.get("id") or item.get("metadata", {}).get("doc_id") or hashlib.sha1(str(item).encode("utf-8")).hexdigest()
            merged[chunk_id] = item
        return list(merged.values())

    def _confidence_from_evidence(self, evidence: Dict[str, Any]) -> float:
        score = float(evidence.get("score", 0.0) or 0.0)
        confidence = 1.0 / (1.0 + score)
        return max(0.1, min(0.99, confidence))


def run_research_loop(*, settings: Any, vector_store: Any, graph_repo: Any, embedding_provider: Any, provider: Any, question: str, top_k: int = 6) -> AskResponse:
    """Public entry point used by the /ask route."""
    graph = ResearchGraph(
        settings=settings,
        vector_store=vector_store,
        graph_repo=graph_repo,
        embedding_provider=embedding_provider,
        provider=provider,
    )
    return graph.run(question, top_k=top_k)
