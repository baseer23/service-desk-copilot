import pytest

from backend.app.agent.models import (
    GeneralAnswer,
    InvestigationDecision,
    InvestigationPlan,
    RequestIntent,
    VerificationResult,
)
from backend.app.models.instructions import stage_instructions
from backend.app.models.dto import RequestResult

pytestmark = pytest.mark.integration


def test_empty_legacy_final_response_is_restored_as_absent():
    restored = RequestResult.model_validate(
        {"ticket": {}, "status": "assessment_ready", "final_response": {}}
    )
    assert restored.final_response is None


def install_conversation_provider(client, captured):
    agent = client.app.state.agent
    original = agent.provider.generate_structured

    async def generate(stage, context, schema):
        captured.append((stage, context))
        if stage == "classify_intent":
            return RequestIntent(
                workflow="knowledge",
                knowledge_source="general",
                goal="Geography",
                justification="General question",
            )
        if stage == "general_chat":
            assert schema is GeneralAnswer
            return GeneralAnswer(answer="Toronto is in Ontario, Canada.")
        if stage == "plan":
            return InvestigationPlan(
                goal="Answer the general-knowledge question directly",
                additional_evidence_useful=False,
                stop_condition="A concise direct answer is ready",
            )
        if stage == "reason":
            return InvestigationDecision(
                outcome="answer",
                evidence_summary="No external evidence requested for this general answer.",
                enough_information=True,
                answer="Toronto is in Ontario, Canada.",
                confidence=0.8,
            )
        if stage == "verify":
            return VerificationResult(
                supported_claims=["Toronto is in Ontario, Canada."],
                confidence=0.8,
                should_continue_research=False,
                justification="Stable general knowledge was answered directly.",
            )
        return await original(stage, context, schema)

    agent.provider.generate_structured = generate


def test_general_chat_uses_graph_without_fabricated_sources(make_client):
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
    assert body["result"]["final_response"]["sources"] == ["request_report"]
    assert calls == ["search_semantic_memory", "list_available_context"]
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
    history = restored.get("/conversations")
    assert history.status_code == 200
    assert history.json()[0]["conversation_id"] == body["request_id"]
    detail = restored.get(f'/conversations/{body["request_id"]}')
    assert detail.status_code == 200
    assert detail.json()["result"]["chat_messages"][0]["content"] == "Where is Toronto?"
    captured = []
    install_conversation_provider(restored, captured)
    response = restored.post(
        f'/requests/{body["request_id"]}/message', json={"summary": "Tell me more."}
    )
    assert response.status_code == 200
    assert captured[-1][1]["conversation"][0]["content"] == "Where is Toronto?"
    assert restored.post("/requests/missing/message", json={"summary": "Hello"}).status_code == 404
    assert restored.get("/conversations/missing").status_code == 404


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


@pytest.mark.memory
def test_two_general_conversations_are_listed_and_reopen_independently(make_client):
    client = make_client({"KNOWLEDGE_SCOPE": "broad"})
    install_conversation_provider(client, [])
    first = client.post("/ask", json={"summary": "Where is Toronto?"}).json()
    second = client.post("/ask", json={"summary": "Where is Ottawa?"}).json()
    history = client.get("/conversations").json()
    assert {item["conversation_id"] for item in history} == {
        first["request_id"], second["request_id"]
    }
    for response, question in ((first, "Where is Toronto?"), (second, "Where is Ottawa?")):
        restored = client.get(f'/conversations/{response["request_id"]}').json()
        assert restored["request_id"] == response["request_id"]
        assert restored["result"]["chat_messages"][0]["content"] == question


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


def test_public_news_intent_routes_to_research_and_keeps_followup_context(make_client):
    client = make_client({"KNOWLEDGE_SCOPE": "broad", "ENABLE_PUBLIC_WEB_TOOLS": "true"})
    agent = client.app.state.agent
    assert agent.intent_route(
        {
            "intent": {
                "workflow": "knowledge",
                "knowledge_source": "public_web",
                "public_topic": "news",
                "in_scope": True,
            }
        }
    ) == "understand_request"
    assert "public_web" in stage_instructions(
        "classify_intent", {"knowledge_scope": "broad"}
    )
    assert "conversation" in stage_instructions(
        "classify_intent", {"knowledge_scope": "broad"}
    )


def test_public_news_request_uses_news_and_fetches_sources_without_local_retrieval(make_client):
    client = make_client({"KNOWLEDGE_SCOPE": "broad", "ENABLE_PUBLIC_WEB_TOOLS": "true"})
    agent = client.app.state.agent
    original = agent.harness.invoke
    calls = []

    async def public_tools(name, arguments):
        calls.append(name)
        if name == "search_news":
            return [
                {
                    "title": f"Alberta headline {number}",
                    "url": f"https://news{number}.example/alberta",
                    "snippet": "A current Alberta report.",
                    "published_at": f"2026-10-{18 - number:02d}T12:00:00Z",
                    "publisher": (
                        f"Publisher {number} with detailed regional coverage, local context, "
                        "and independently reported developments"
                    ),
                    "source_system": "public_web",
                }
                for number in range(1, 4)
            ]
        if name == "fetch_web_page":
            number = arguments["url"].split("news", 1)[1].split(".", 1)[0]
            return {
                "url": arguments["url"],
                "title": f"Alberta headline {number}",
                "text": "Verified reporting about a current Alberta development. " * 4,
                "source_system": "public_web",
            }
        return await original(name, arguments)

    agent.harness.invoke = public_tools
    response = client.post("/ask", json={"summary": "What's going on in Alberta right now?"})
    assert response.status_code == 200
    result = response.json()["result"]
    assert calls.count("search_news") == 1
    assert calls.count("fetch_web_page") == 3
    assert "search_knowledge" not in calls and "search_graph" not in calls
    assert result["final_answer"].count("Alberta headline") == 3
    assert "2026-10-" in result["final_answer"]
    assert len(result["final_answer"]) > 280
    assert {
        "https://news1.example/alberta",
        "https://news2.example/alberta",
        "https://news3.example/alberta",
    }.issubset(result["final_response"]["sources"])


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
    assert agent.verification_route(state) == "finalize"
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
    assert contexts[1]["plan_validation_feedback"]["validation_error_code"] == "INVALID_TOOL_ARGUMENTS"
    assert contexts[1]["plan_validation_feedback"]["tool_name"] == "search_knowledge"
    assert contexts[1]["plan_validation_feedback"]["actual_argument_names"] == ["mode", "question"]
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
