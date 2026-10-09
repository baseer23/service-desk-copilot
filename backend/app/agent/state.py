from typing import Any, TypedDict


class InvestigationState(TypedDict, total=False):
    case_id: str
    ticket: dict[str, Any]
    status: str
    workflow: str
    intent: dict[str, Any]
    analysis: dict[str, Any]
    plan: dict[str, Any]
    plan_executed: bool
    decision_issues: list[str]
    decision: dict[str, Any]
    verification: dict[str, Any]
    evidence: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    available_context: dict[str, Any]
    remembered_facts: list[dict[str, Any]]
    confirmed_facts: list[dict[str, Any]]
    human_answers: list[dict[str, Any]]
    hypotheses: list[dict[str, Any]]
    inferences: list[str]
    contradictions: list[str]
    missing_information: list[dict[str, Any]]
    pending_question: str | None
    pending_field: str | None
    research_iteration: int
    research_rounds: int
    actions_already_taken: list[str]
    confidence: float
    likely_issue: str | None
    recommended_next_action: str | None
    final_answer: str | None
    final_response: dict[str, Any]
    uncertainty: list[str]
