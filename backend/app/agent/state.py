from typing import Any, TypedDict


class InvestigationState(TypedDict, total=False):
    """The sole durable LangGraph case state; all values serialize to JSON."""

    case_id: str
    ticket: dict[str, Any]
    status: str
    analysis: dict[str, Any]
    decision: dict[str, Any]
    evidence: list[dict[str, Any]]
    previous_tickets: list[dict[str, Any]]
    knowledge: list[dict[str, Any]]
    alerts: list[dict[str, Any]]
    changes: list[dict[str, Any]]
    web_sources: list[dict[str, Any]]
    remembered_facts: list[dict[str, Any]]
    confirmed_facts: list[dict[str, Any]]
    human_answers: list[dict[str, Any]]
    hypotheses: list[str]
    inferences: list[str]
    missing_information: list[str]
    pending_question: str | None
    pending_field: str | None
    research_iteration: int
    context_site: str | None
    requested_tools: list[dict[str, Any]]
    tool_events: list[dict[str, Any]]
    confidence: float
    likely_issue: str | None
    recommended_next_action: str | None
    final_answer: str | None
    uncertainty: list[str]
