from __future__ import annotations

from typing import Any, Dict, List, TypedDict


class InvestigationState(TypedDict, total=False):
    """Durable investigation data for a single incident case."""

    case_id: str
    ticket: Dict[str, Any]
    conversation: List[Dict[str, Any]]
    identified_user: Dict[str, Any]
    identified_company: Dict[str, Any]
    identified_site: Dict[str, Any]
    identified_device: Dict[str, Any]
    extracted_issue: Dict[str, Any]
    evidence: List[Dict[str, Any]]
    previous_tickets: List[Dict[str, Any]]
    knowledge: List[Dict[str, Any]]
    alerts: List[Dict[str, Any]]
    changes: List[Dict[str, Any]]
    web_sources: List[Dict[str, Any]]
    remembered_facts: List[Dict[str, Any]]
    hypotheses: List[Dict[str, Any]]
    missing_information: List[str]
    pending_question: str | None
    human_answers: List[Dict[str, Any]]
    actions_taken: List[str]
    tool_events: List[Dict[str, Any]]
    research_iteration: int
    confidence: float
    final_answer: str | None
    uncertainty: List[str]
