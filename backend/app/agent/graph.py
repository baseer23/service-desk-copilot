from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.app.agent.memory import CaseMemoryStore
from backend.app.agent.tools import AgentToolHarness
from backend.app.mcp.client import DeskMateMCPClient

logger = logging.getLogger("service-desk")


class CaseGraphState(TypedDict, total=False):
    case_id: str
    ticket: dict[str, Any]
    question: str
    evidence: list[dict[str, Any]]
    remembered_facts: list[dict[str, Any]]
    missing_information: list[str]
    pending_question: str | None
    status: str
    final_answer: str | None
    confidence: float
    uncertainty: list[str]


class ServiceDeskAgent:
    """Stateful service-desk investigation workflow built on LangGraph."""

    def __init__(self, *, mcp_client: DeskMateMCPClient | None = None, memory_store: CaseMemoryStore | None = None) -> None:
        self.mcp = mcp_client or DeskMateMCPClient()
        self.memory = memory_store or CaseMemoryStore()
        self._tool_harness = AgentToolHarness(self.mcp, self.memory)

    def build_graph(self) -> StateGraph:
        graph = StateGraph(CaseGraphState)
        graph.add_node("analyze_ticket", self._analyze_ticket)
        graph.add_node("collect_evidence", self._collect_evidence)
        graph.add_node("decide_next_step", self._decide_next_step)
        graph.add_node("finalize", self._finalize)

        graph.add_edge(START, "analyze_ticket")
        graph.add_edge("analyze_ticket", "collect_evidence")
        graph.add_conditional_edges(
            "collect_evidence",
            self._route_after_evidence,
            {"decide": "decide_next_step", "finalize": "finalize"},
        )
        graph.add_conditional_edges(
            "decide_next_step",
            self._route_after_decision,
            {"finalize": "finalize", "decide": "collect_evidence"},
        )
        graph.add_edge("finalize", END)
        return graph

    def run_case(self, case_id: str, ticket: dict[str, Any], question: str | None = None) -> dict[str, Any]:
        initial_state: CaseGraphState = {
            "case_id": case_id,
            "ticket": ticket,
            "question": question or str(ticket.get("summary") or ticket.get("description") or "Need assistance"),
            "evidence": [],
            "remembered_facts": [],
            "missing_information": [],
            "pending_question": None,
            "status": "active",
            "final_answer": None,
            "confidence": 0.0,
            "uncertainty": [],
        }
        existing = self.memory.load_case(case_id)
        if existing:
            initial_state.update(existing)
            if ticket:
                initial_state["ticket"] = ticket
        if question:
            initial_state["question"] = question
        compiled = self.build_graph().compile()
        result = compiled.invoke(initial_state)
        payload = dict(result)
        payload.setdefault("case_id", case_id)
        self.memory.save_case(case_id, payload)
        self.memory.record_event(
            case_id,
            "service_desk_agent",
            "agent",
            str(payload.get("final_answer") or payload.get("question") or "investigation updated"),
        )
        return payload

    def _analyze_ticket(self, state: CaseGraphState) -> dict[str, Any]:
        ticket = state.get("ticket", {}) or {}
        summary = (ticket.get("summary") or ticket.get("description") or "").strip()
        if summary:
            state["question"] = state.get("question") or summary
        return {
            "missing_information": self._infer_missing_information(ticket),
            "uncertainty": ["Need to verify user/site/device details before finalizing."],
            "status": "analyzing",
        }

    def _collect_evidence(self, state: CaseGraphState) -> dict[str, Any]:
        ticket = state.get("ticket", {})
        question = state.get("question") or "service desk case investigation"
        evidence: list[dict[str, Any]] = []
        try:
            overview = self.mcp.search_knowledge(question, "HYBRID", top_k=5)
            if isinstance(overview, list):
                evidence.extend(overview)
        except Exception as exc:  # pragma: no cover - network dependent
            logger.warning("Knowledge retrieval failed for case investigation: %s", exc)

        user_name = ticket.get("user") or ticket.get("user_name")
        if user_name:
            try:
                user_ctx = asyncio.run(self.mcp.get_user_context_async(user_name, ticket.get("company")))
                evidence.append({"source": "user_context", "data": user_ctx})
            except Exception as exc:  # pragma: no cover - network dependency
                logger.warning("User context request failed: %s", exc)

        company = ticket.get("company")
        site = ticket.get("site")
        if company:
            try:
                if site:
                    evidence.append({"source": "site_context", "data": asyncio.run(self.mcp.get_site_context_async(company, site))})
                evidence.append({"source": "company_context", "data": asyncio.run(self.mcp.get_company_context_async(company))})
            except Exception as exc:  # pragma: no cover - network dependency
                logger.warning("Company/site context request failed: %s", exc)

        remembered = self.memory.list_facts_for_case(state.get("case_id")) if state.get("case_id") else []
        return {
            "evidence": evidence,
            "remembered_facts": remembered,
            "status": "gathered_evidence",
        }

    def _decide_next_step(self, state: CaseGraphState) -> dict[str, Any]:
        ticket = state.get("ticket", {})
        missing = self._infer_missing_information(ticket)
        evidence = state.get("evidence", [])
        if missing:
            return {
                "pending_question": "I need a quick verification: " + "; ".join(missing),
                "missing_information": missing,
                "status": "waiting_for_human",
                "uncertainty": missing,
            }
        if not evidence:
            return {
                "pending_question": "I don't yet have enough evidence for this issue. Do you want me to continue with public web research?",
                "status": "needs_review",
                "confidence": 0.35,
                "uncertainty": ["No local evidence was found."],
            }
        return {
            "status": "ready",
            "confidence": 0.82,
            "uncertainty": [],
        }

    def _finalize(self, state: CaseGraphState) -> dict[str, Any]:
        ticket = state.get("ticket", {})
        summary = (ticket.get("summary") or ticket.get("description") or "Service request")
        evidence_items = state.get("evidence", [])
        answer = (
            f"Investigation for case {state.get('case_id', 'unknown')}: "
            f"I reviewed the available service-desk evidence for '{summary}'. "
            f"The current record suggests a likely issue in the affected user/site/device path, and I have captured {len(evidence_items)} evidence items for follow-up."
        )
        return {
            "final_answer": answer,
            "status": "resolved",
            "confidence": float(state.get("confidence", 0.82) or 0.82),
        }

    def _route_after_evidence(self, state: CaseGraphState) -> str:
        if not state.get("evidence"):
            return "finalize"
        return "decide"

    def _route_after_decision(self, state: CaseGraphState) -> str:
        if state.get("pending_question") or state.get("missing_information"):
            return "decide"
        return "finalize"

    def _infer_missing_information(self, ticket: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        if not (ticket.get("user") or ticket.get("user_name")):
            missing.append("user name")
        if not ticket.get("company"):
            missing.append("company")
        if not ticket.get("site"):
            missing.append("site")
        if not ticket.get("device"):
            missing.append("device")
        if not ticket.get("summary") and not ticket.get("description"):
            missing.append("issue summary")
        return missing

    def _remembered_facts_for_case(self, case_id: str | None) -> list[dict[str, Any]]:
        if not case_id:
            return []
        try:
            return self.memory.list_facts_for_case(case_id)
        except Exception:  # pragma: no cover - defensive fallback
            return []


__all__ = ["CaseGraphState", "ServiceDeskAgent"]
