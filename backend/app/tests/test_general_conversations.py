from backend.app.agent.models import GeneralAnswer, RequestIntent
from backend.app.models.instructions import stage_instructions


def install_conversation_provider(client, captured):
    agent = client.app.state.agent

    async def generate(stage, context, schema):
        captured.append((stage, context))
        if stage == "classify_intent":
            return RequestIntent(
                workflow="knowledge",
                knowledge_source="general",
                goal="Geography",
                justification="General question",
            )
        assert stage == "general_chat"
        assert schema is GeneralAnswer
        return GeneralAnswer(answer="Toronto is in Ontario, Canada.")

    agent.provider.generate_structured = generate


def test_general_chat_uses_graph_without_tools_or_fabricated_sources(make_client):
    client = make_client({"KNOWLEDGE_SCOPE": "broad"})
    captured = []
    install_conversation_provider(client, captured)
    calls = []
    original = client.app.state.agent.harness.invoke

    async def record(name, arguments):
        calls.append(name)
        return await original(name, arguments)

    client.app.state.agent.harness.invoke = record
    response = client.post("/ask", json={"summary": "Where is Toronto?"})
    assert response.status_code == 200
    body = response.json()
    assert body["case_id"] is None
    assert body["result"]["final_answer"] == "Toronto is in Ontario, Canada."
    assert body["result"]["final_response"]["sources"] == []
    assert calls == []
    followup = client.post(
        f'/requests/{body["request_id"]}/message', json={"summary": "Which province is that?"}
    )
    assert followup.status_code == 200
    assert captured[-1][1]["conversation"][-1]["content"] == "Toronto is in Ontario, Canada."
    assert client.get("/cases").json() == []


def test_general_conversation_survives_restart_and_missing_id_is_safe(make_client):
    first = make_client({"KNOWLEDGE_SCOPE": "broad"})
    install_conversation_provider(first, [])
    body = first.post("/ask", json={"summary": "Where is Toronto?"}).json()
    restored = make_client({"KNOWLEDGE_SCOPE": "broad"})
    captured = []
    install_conversation_provider(restored, captured)
    response = restored.post(
        f'/requests/{body["request_id"]}/message', json={"summary": "Tell me more."}
    )
    assert response.status_code == 200
    assert captured[-1][1]["conversation"][0]["content"] == "Where is Toronto?"
    assert restored.post("/requests/missing/message", json={"summary": "Hello"}).status_code == 404


def test_general_conversation_followup_is_owner_scoped(make_client):
    import pytest
    from backend.app.core.principal import Principal, principal_scope

    client = make_client({"KNOWLEDGE_SCOPE": "broad"})
    install_conversation_provider(client, [])
    response = client.post("/ask", json={"summary": "Where is Toronto?"}).json()
    agent = client.app.state.agent

    async def other_user():
        with principal_scope(Principal("local-demo", "another-user")):
            await agent.run_request(response["request_id"], message="Read that conversation")

    with pytest.raises(KeyError):
        client.portal.call(other_user)


def test_support_scope_cannot_enter_general_generation(make_client):
    client = make_client()
    captured = []
    install_conversation_provider(client, captured)
    agent = client.app.state.agent
    assert (
        agent.intent_route(
            {"intent": {"workflow": "knowledge", "knowledge_source": "general", "in_scope": False}}
        )
        == "reject_scope"
    )
    assert "General conversation is enabled" in stage_instructions(
        "classify_intent", {"knowledge_scope": "broad"}
    )
    assert "in_scope=false" in stage_instructions("classify_intent", {"knowledge_scope": "support"})


def test_pending_question_with_rejected_claims_is_revised_not_abandoned(make_client):
    client = make_client()
    agent = client.app.state.agent
    state = {
        "research_iteration": 1,
        "decision": {"question_for_human": "What error appears?"},
        "verification": {
            "unsupported_claims": ["Unconfirmed cause stated as fact"],
            "contradictions": [],
            "should_continue_research": False,
        },
    }
    assert agent.verification_route(state) == "reason"
    state["decision"]["question_for_human"] = None
    assert agent.verification_route(state) == "reason"
    state["research_iteration"] = agent.settings.max_investigation_cycles
    assert agent.verification_route(state) == "finalize"


def test_discovered_mcp_enums_are_validated_before_dispatch():
    import pytest
    from backend.app.agent.tools import annotation
    from pydantic import TypeAdapter, ValidationError

    validator = TypeAdapter(
        annotation({"type": "string", "enum": ["VECTOR", "GRAPH", "HYBRID", "KEYWORD"]})
    )
    assert validator.validate_python("HYBRID") == "HYBRID"
    with pytest.raises(ValidationError):
        validator.validate_python("semantic")


def test_search_evidence_accepts_real_azure_null_scores(make_client):
    client = make_client()
    rows = client.app.state.agent._evidence(
        {"case_id": "fictional-score"},
        "search_knowledge",
        {
            "source_system": "azure_search",
            "timestamp": "2026-10-13T00:00:00Z",
            "reliability": 0.9,
            "items": [
                {"id": "one", "text": "Fictional VPN guidance", "score": None, "metadata": {}}
            ],
        },
    )
    assert rows[0]["relevance"] == 1


def test_invalid_model_plan_gets_one_bounded_schema_revision_without_dispatch(make_client):
    from backend.app.agent.models import InvestigationPlan

    client = make_client()
    agent = client.app.state.agent
    contexts = []

    async def generate(stage, context, schema):
        contexts.append(dict(context))
        return InvestigationPlan(
            goal="Find guidance",
            tool_calls=[
                {
                    "name": "search_knowledge",
                    "arguments": {
                        "question": "VPN",
                        "mode": "semantic" if len(contexts) == 1 else "HYBRID",
                    },
                }
            ],
            stop_condition="Relevant guidance",
        )

    agent.provider.generate_structured = generate
    state = {
        "case_id": "fictional-plan",
        "persist_case": False,
        "workflow": "knowledge",
        "ticket": {"summary": "VPN"},
        "evidence": [],
    }
    before = agent.harness.invocation_count
    result = client.portal.call(agent.plan, state)
    assert len(contexts) == 2
    assert contexts[1]["plan_validation_feedback"][0]["type"] == "literal_error"
    assert "input" not in contexts[1]["plan_validation_feedback"][0]
    assert result["plan"]["tool_calls"][0]["arguments"]["mode"] == "HYBRID"
    assert agent.harness.invocation_count == before


def test_clarification_is_verified_without_publishing_unreviewed_diagnosis(make_client):
    from backend.app.agent.models import VerificationResult

    client = make_client()
    agent = client.app.state.agent

    async def generate(stage, context, schema):
        assert stage == "verify"
        assert context["verification_target"] == "clarification"
        assert context["decision"]["hypotheses"] == []
        assert context["decision"]["answer"] is None
        assert context["decision"]["question_for_human"] == "Which VPN client are you using?"
        return VerificationResult(
            supported_claims=[],
            unsupported_claims=[],
            evidence_ids=[],
            confidence=0.5,
            should_continue_research=False,
            justification="A useful diagnostic clarification",
        )

    agent.provider.generate_structured = generate
    state = {
        "case_id": "fictional-clarification",
        "persist_case": False,
        "workflow": "service_desk",
        "ticket": {"summary": "VPN authentication failed"},
        "evidence": [],
        "decision": {
            "question_for_human": "Which VPN client are you using?",
            "human_field": "vpn_client",
            "question_reason": "Select the correct guidance",
            "hypotheses": [{"claim": "An unconfirmed cause"}],
        },
    }
    result = client.portal.call(agent.verify, state)
    assert result["hypotheses"] == [] and result["likely_issue"] is None
