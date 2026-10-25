from typing import Any, Literal, TypedDict


class InvestigationState(TypedDict, total=False):
    case_id: str
    tenant_id: str
    voice_intake: dict[str, Any]
    origin: Literal["user", "demo", "test", "unknown"]
    persist_case: bool
    route_request: bool
    ticket: dict[str, Any]
    status: str
    workflow: str
    intent: dict[str, Any]
    analysis: dict[str, Any]
    plan: dict[str, Any]
    research_questions: list[str]
    plan_executed: bool
    decision_issues: list[str]
    decision: dict[str, Any]
    verification: dict[str, Any]
    evidence: list[dict[str, Any]]
    retrieval_candidates: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    prefetch_results: list[dict[str, Any]]
    tools_discovered: bool
    executed_tool_calls: list[str]
    available_context: dict[str, Any]
    remembered_facts: list[dict[str, Any]]
    personal_memory: list[dict[str, Any]]
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
    web_pages_fetched: int
    actions_already_taken: list[str]
    confidence: float
    likely_issue: str | None
    recommended_next_action: str | None
    final_answer: str | None
    final_response: dict[str, Any]
    uncertainty: list[str]
    chat_messages: list[dict[str, str]]
    conversation_context: dict[str, Any]
    request_owner: str
