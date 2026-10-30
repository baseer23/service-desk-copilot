"""Focused regressions for plan/evidence validation and personal identity lookup."""

import pytest

from backend.app.agent.models import (
    InvestigationDecision,
    InvestigationPlan,
    RequestIntent,
    VerificationResult,
)
from backend.app.models.provider import LLMError


def identity_state(summary="whats my name"):
    return {
        "case_id": "identity-validation",
        "persist_case": False,
        "workflow": "knowledge",
        "ticket": {"summary": summary},
        "intent": {"knowledge_source": "general"},
        "chat_messages": [],
        "evidence": [],
    }


def test_identity_question_recovers_to_exact_authenticated_profile_schema(make_client):
    client = make_client({"LOCAL_PERSISTENCE_ENABLED": "true"})
    agent = client.app.state.agent
    contexts = []

    async def invalid_plan(stage, context, schema):
        assert stage == "plan"
        contexts.append(context.copy())
        return InvestigationPlan(
            goal="Check personal memory",
            additional_evidence_useful=False,
            tool_calls=[{
                "name": "get_authenticated_profile",
                "arguments": {"query": "name"},
                "reason": "Check the authenticated profile",
            }],
            stop_condition="A confirmed name or absence is known",
        )

    agent.provider.generate_structured = invalid_plan
    update = client.portal.call(agent.plan, identity_state())

    assert len(contexts) == 2
    feedback = contexts[1]["plan_validation_feedback"]
    assert feedback["validation_error_code"] == "INVALID_TOOL_ARGUMENTS"
    assert feedback["tool_name"] == "get_authenticated_profile"
    assert feedback["expected_argument_names"] == []
    assert feedback["actual_argument_names"] == ["query"]
    call = update["plan"]["tool_calls"][0]
    assert call == {
        "name": "get_authenticated_profile",
        "arguments": {},
        "reason": "The answer must come from confirmed personal memory.",
    }
    assert update["plan"]["additional_evidence_useful"] is True


def test_non_identity_plan_failure_retains_safe_structured_diagnostics(make_client):
    client = make_client()
    agent = client.app.state.agent

    async def invalid_plan(stage, context, schema):
        return InvestigationPlan(
            goal="Invalid lookup",
            tool_calls=[{"name": "not_a_capability", "arguments": {}}],
            stop_condition="Never",
        )

    agent.provider.generate_structured = invalid_plan
    try:
        client.portal.call(agent.plan, {
            **identity_state("Explain a stable concept"),
            "case_id": "diagnostic-validation",
        })
    except LLMError as error:
        assert error.category == "application_validation"
        assert error.diagnostics == {
            "request_id": "diagnostic-validation",
            "provider": "stub",
            "stage": "plan",
            "attempt_number": 2,
            "validation_error_code": "TOOL_NOT_PERMITTED",
            "tool_name": "not_a_capability",
            "expected_argument_names": [],
            "actual_argument_names": [],
        }
    else:
        raise AssertionError("invalid non-identity plan was accepted")


def test_unknown_evidence_ids_get_exact_feedback_then_safe_uncertainty(make_client):
    client = make_client()
    agent = client.app.state.agent
    contexts = []

    async def invalid_reason(stage, context, schema):
        assert stage == "reason"
        contexts.append(context.copy())
        return InvestigationDecision(
            outcome="answer",
            hypotheses=[{
                "claim": "Unsupported claim",
                "evidence_ids": ["invented-id"],
                "status": "supported",
            }],
            evidence_summary="Unsupported",
            enough_information=True,
            answer="Unsupported claim",
            confidence=0.9,
        )

    agent.provider.generate_structured = invalid_reason
    state = {
        **identity_state("What does the evidence show?"),
        "plan": InvestigationPlan(
            goal="Assess evidence",
            additional_evidence_useful=True,
            stop_condition="Evidence assessed",
        ).model_dump(),
        "evidence": [{
            "id": "permitted-id", "source_type": "document", "tool": "search_knowledge",
            "content": {"text": "Permitted evidence"}, "source": "test",
            "source_system": "test", "timestamp": "2026-10-26T00:00:00Z",
            "observed_at": "2026-10-26T00:00:00Z", "reliability": 1,
        }],
        "research_iteration": 0,
    }
    update = client.portal.call(agent.reason, state)

    assert "invented-id" in contexts[1]["reason_validation_feedback"]
    assert "permitted-id" in contexts[1]["reason_validation_feedback"]
    assert update["decision"]["outcome"] == "cannot_verify"
    assert update["decision"]["answer"] is None
    assert update["decision"]["hypotheses"][0]["evidence_ids"] == []
    assert update["confidence"] == 0


@pytest.mark.parametrize(
    ("saved_name", "expected"),
    [
        (None, "I don't currently have a confirmed name for you."),
        ("Fictional Ada", "Your confirmed name is Fictional Ada."),
    ],
)
def test_personal_identity_answers_from_confirmed_profile_or_reports_unknown(
    make_client, saved_name, expected
):
    client = make_client({"LOCAL_PERSISTENCE_ENABLED": "true"})
    if saved_name:
        saved = client.post(
            "/memory/save",
            json={"text": f"My name is {saved_name}.", "conversation_id": "identity-fixture"},
        )
        assert saved.status_code == 200
    agent = client.app.state.agent

    async def provider(stage, context, schema):
        if stage == "classify_intent":
            return RequestIntent(
                workflow="knowledge",
                knowledge_source="general",
                goal="Report the authenticated user's confirmed name",
                justification="Personal identity requires confirmed scoped memory.",
            )
        if stage == "plan":
            # Exercise the bounded correction and deterministic safe profile fallback.
            return InvestigationPlan(
                goal="Check personal memory",
                additional_evidence_useful=False,
                tool_calls=[{
                    "name": "get_authenticated_profile",
                    "arguments": {"query": "name"},
                    "reason": "Check confirmed profile memory",
                }],
                stop_condition="A confirmed name or absence is known",
            )
        if stage == "reason":
            profile = next(
                item for item in context["evidence"]
                if item["tool"] == "get_authenticated_profile"
            )
            name = profile["content"].get("name")
            answer = (
                f"Your confirmed name is {name}."
                if name else "I don't currently have a confirmed name for you."
            )
            return InvestigationDecision(
                outcome="answer",
                hypotheses=[{
                    "claim": answer,
                    "evidence_ids": [profile["id"]],
                    "status": "supported",
                    "confidence": 1,
                }],
                evidence_summary="Authenticated profile checked.",
                enough_information=True,
                answer=answer,
                confidence=1,
            )
        if stage == "verify":
            evidence_id = next(
                item["id"] for item in context["evidence"]
                if item["tool"] == "get_authenticated_profile"
            )
            return VerificationResult(
                supported_claims=[context["decision"]["answer"]],
                evidence_ids=[evidence_id],
                confidence=1,
                should_continue_research=False,
                justification="The answer matches the authenticated profile observation.",
            )
        raise AssertionError(f"unexpected model stage: {stage}")

    agent.provider.generate_structured = provider
    response = client.post("/ask", json={"summary": "whats my name"})

    assert response.status_code == 200, response.json()
    assert response.json()["result"]["final_answer"] == expected
