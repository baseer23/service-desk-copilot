import asyncio
from datetime import datetime, timezone
import pytest

from backend.app.agent.graph import concise_answer_line
from langchain_core.tools import StructuredTool
from backend.app.agent.models import (
    EvidenceGradeResult,
    InvestigationPlan,
    VerificationResult,
    MemoryFact,
)
from backend.app.mcp.client import DeskMateMCPClient

RESUME = "Abdul Khan — Resume. Education: Graduated in 2022 from Dalhousie University. Skills: Python, React, SQL."


def run(client, text):
    created = client.post("/cases", json={"summary": text})
    assert created.status_code == 201
    cid = created.json()["case_id"]
    response = client.post(f"/cases/{cid}/investigate")
    assert response.status_code == 200, response.text
    return cid, response.json()


@pytest.mark.parametrize(
    "question",
    [
        "What year did Abdul graduate?",
        "What skills are listed on Abdul's resume?",
        "Summarize this PDF.",
    ],
)
def test_document_questions_use_real_knowledge_subgraph(make_client, question):
    c = make_client()
    assert (
        c.post("/ingest/paste", json={"title": "Abdul resume", "text": RESUME}).status_code == 200
    )
    cid, result = run(c, question)
    assert result["workflow"] == "knowledge" and result["status"] == "assessment_ready"
    assert not result["pending_question"]
    assert "2022" in result["final_response"]["answer"]
    events = c.get(f"/cases/{cid}/events").json()
    tools = {e["tool"] for e in events}
    assert {
        "understand_question",
        "plan",
        "search_knowledge",
        "observe",
        "verify",
        "finalize",
    } <= tools
    assert (
        not {"analyze_ticket", "get_user_context", "get_site_context", "get_active_alerts"} & tools
    )
    agent = c.app.state.agent
    assert "knowledge_subgraph" in agent.graph.get_graph().nodes
    assert "formulate_retrieval_plan" in agent.knowledge_subgraph.get_graph().nodes


@pytest.mark.parametrize(
    "ticket,expected_tool,forbidden",
    [
        ("Sarah cannot complete Microsoft 365 MFA.", "get_user_context", "current_site"),
        ("Accounting cannot print invoices to PRN-ACCT-02.", "get_device_context", "current_site"),
        ("Five users at B99 report VPN failure.", "get_active_alerts", "current_site"),
    ],
)
def test_generalized_cases_choose_relevant_capabilities(
    make_client, ticket, expected_tool, forbidden
):
    c = make_client()
    cid, result = run(c, ticket)
    assert result["workflow"] == "service_desk"
    assert result["status"] == "assessment_ready"
    assert not result["pending_question"]
    events = c.get(f"/cases/{cid}/events").json()
    assert any(e["tool"] == expected_tool for e in events)
    assert any(e["tool"] == "verify" for e in events)
    assert "floor" not in (result["recommended_next_action"] or "").lower()
    if "Five users" in ticket:
        calls = [e["tool"] for e in events]
        assert calls.index("get_active_alerts") < calls.index("reason")
        assert "get_device_context" not in calls


def test_no_single_tool_supplies_cause_and_agent_correlates(make_client):
    c = make_client()
    cid, first = run(c, "Jake Peralta from B99 cannot connect to VPN.")
    assert first["status"] == "needs_input"
    second = c.post(f"/cases/{cid}/resume", json={"answer": "2nd floor"}).json()
    hypothesis = second["hypotheses"][0]
    assert "plausible cause" in hypothesis["claim"] and "not yet confirmed" in hypothesis["claim"]
    assert len(hypothesis["evidence_ids"]) >= 3
    assert "update" in second["pending_question"].lower()
    rows = second["evidence"]
    assert not any(
        "caused" in str(e["content"]).lower() for e in rows if e["source_system"] == "local_fixture"
    )
    assert any(e["source_type"] == "human_confirmation" for e in rows)


def test_all_agent_capabilities_cross_structured_tool_and_real_mcp(make_client, monkeypatch):
    # Parent process repository calls fail, while the independent real MCP
    # subprocess owns its repositories and continues working.
    from backend.app.agent.memory import CaseMemoryStore
    from backend.app.store.vector_chroma import VectorChromaStore
    from backend.app.repositories.fixtures import FixtureRepository
    from backend.app.services.ingest_service import IngestService

    def forbidden(*args, **kwargs):
        raise AssertionError("Direct repository/capability access from application process")

    for cls, names in [
        (CaseMemoryStore, ["load_case", "save_case", "search_facts", "record_event"]),
        (VectorChromaStore, ["__init__"]),
        (IngestService, ["__init__"]),
        (FixtureRepository, ["get_user_context", "get_active_alerts", "get_recent_changes"]),
    ]:
        for name in names:
            monkeypatch.setattr(cls, name, forbidden)
    invoked = []
    original = StructuredTool.ainvoke

    async def traced(self, *args, **kwargs):
        invoked.append(self.name)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(StructuredTool, "ainvoke", traced)
    c = make_client()
    assert invoked == ["get_local_storage_status"]
    invoked.clear()  # Compare investigation calls after startup readiness finished.
    protocol = []
    original_protocol = c.app.state.mcp.call_tool

    async def track(name, args):
        protocol.append(name)
        return await original_protocol(name, args)

    monkeypatch.setattr(c.app.state.mcp, "call_tool", track)
    cid, result = run(c, "Jake Peralta from B99 cannot connect to VPN.")
    assert result["status"] == "needs_input"
    assert {
        "search_memory",
        "search_previous_tickets",
        "get_active_alerts",
        "save_case_note",
        "save_case",
    } <= set(invoked)
    assert invoked == protocol
    assert "case_subgraph" in c.app.state.agent.graph.get_graph().nodes


@pytest.mark.asyncio
async def test_real_mcp_requests_are_concurrent_on_one_owned_session(settings, monkeypatch):
    from mcp import ClientSession

    active = peak = 0
    original = ClientSession.call_tool

    async def tracked(self, *args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.03)
            return await original(self, *args, **kwargs)
        finally:
            active -= 1

    monkeypatch.setattr(ClientSession, "call_tool", tracked)
    client = DeskMateMCPClient(settings)
    try:
        results = await asyncio.gather(
            *(
                client.call_tool("get_user_context", {"user_name": "Jake Peralta"})
                for _ in range(10)
            )
        )
        assert all(r["status"] == "ok" for r in results)
        assert peak >= 5 and client.connection_count == 1
    finally:
        await client.close()
    assert client._task is None


def test_plan_permissions_argument_validation_and_parallel_groups(make_client):
    c = make_client()
    harness = c.app.state.agent.harness

    def plan(name, args):
        return InvestigationPlan(
            goal="test",
            tool_calls=[{"name": name, "arguments": args}],
            parallel_groups=[[0]],
            stop_condition="evidence",
        )

    for name, args in [
        ("remember_fact", {"fact": {}}),
        ("not_a_tool", {}),
        ("get_user_context", {}),
        ("get_user_context", {"user_name": "Jake", "extra": "bad"}),
    ]:
        with pytest.raises(ValueError):
            harness.validate_plan(plan(name, args), "service_desk")
    with pytest.raises(ValueError):
        harness.validate_plan(plan("get_user_context", {"user_name": "Jake"}), "knowledge")
    with pytest.raises(ValueError):
        InvestigationPlan(goal="bad", tool_calls=[], parallel_groups=[[3]], stop_condition="bad")


def test_execute_tools_ignores_legacy_prefetch_and_runs_only_validated_plan(make_client):
    client = make_client()
    agent = client.app.state.agent
    original = agent.harness.invoke
    calls = []

    async def record(name, arguments):
        calls.append(name)
        return await original(name, arguments)

    agent.harness.invoke = record
    state = {
        "case_id": "route-only",
        "persist_case": False,
        "workflow": "knowledge",
        "plan": InvestigationPlan(
            goal="Inspect connected relationships",
            tool_calls=[{
                "name": "search_graph",
                "arguments": {"entities": ["Infrastructure"]},
                "reason": "Use the selected connected graph source",
            }],
            parallel_groups=[[0]],
            skipped_sources=[{
                "source": "uploaded documents",
                "reason": "Documents cannot establish live graph contents",
            }],
            stop_condition="The connected graph responds",
        ).model_dump(),
        "prefetch_results": [{
            "name": "search_knowledge",
            "arguments": {"question": "unrelated"},
            "output": {"items": [{"text": "irrelevant"}]},
        }],
    }
    update = client.portal.call(agent.execute_tools, state)
    assert [item["name"] for item in update["tool_results"]] == ["search_graph"]
    assert calls == ["search_graph"]


def test_document_grader_batches_candidates_and_rejects_irrelevant_chunks(make_client):
    client = make_client()
    agent = client.app.state.agent
    captured = []

    async def grade(stage, context, schema):
        assert stage == "grade_evidence"
        captured.append(context)
        relevant, irrelevant = context["candidates"]
        return EvidenceGradeResult(
            accepted_ids=[relevant["id"]],
            rejected=[{"id": irrelevant["id"], "reason": "Immigration text does not answer VPN guidance"}],
        )

    agent.provider.generate_structured = grade
    state = {
        "case_id": "grade-documents",
        "persist_case": False,
        "workflow": "knowledge",
        "ticket": {"summary": "What does our uploaded VPN manual say?"},
        "analysis": {"entities": ["VPN"]},
        "evidence": [],
        "tool_results": [{
            "name": "search_knowledge",
            "output": {
                "source_system": "local_knowledge",
                "timestamp": "2026-10-18T00:00:00Z",
                "reliability": 0.9,
                "items": [
                    {"id": "vpn", "text": "VPN manual: refresh the approved profile.",
                     "score": 2, "match_type": "keyword", "metadata": {"title": "VPN manual"}},
                    {"id": "immigration", "text": "Immigration and employment eligibility.",
                     "score": 1, "match_type": "keyword", "metadata": {"title": "Immigration PDF"}},
                ],
            },
        }],
    }
    update = client.portal.call(agent.observe, state)
    assert len(captured) == 1 and len(captured[0]["candidates"]) == 2
    assert [row["content"]["id"] for row in update["evidence"]] == ["vpn"]
    assert update["evidence"][0]["relevance"] == pytest.approx(2 / 3)
    assert agent._source_relevance("search_knowledge", {"score": 0.25}) == 0.8


def test_verifier_blocks_unsupported_claim_without_repeating_same_research(make_client):
    c = make_client()
    c.post("/ingest/paste", json={"title": "Resume", "text": RESUME})
    provider = c.app.state.agent.provider
    original = provider.generate_structured
    count = 0

    async def critic(stage, context, schema):
        nonlocal count
        if stage == "verify":
            count += 1
            if count == 1:
                return VerificationResult(
                    unsupported_claims=["Year needs source verification"],
                    missing_evidence=["Re-read education entry"],
                    confidence=0,
                    should_continue_research=True,
                    justification="Candidate needs independent support",
                )
        return await original(stage, context, schema)

    provider.generate_structured = critic
    cid, result = run(c, "What year did Abdul graduate?")
    assert result["status"] == "insufficient_information" and count == 1
    assert result["research_iteration"] == 1
    assert "Year needs source verification" in result["uncertainty"]
    assert sum(e["tool"] == "search_knowledge" for e in c.get(f"/cases/{cid}/events").json()) == 1


def test_verifier_rejects_unsupported_final_answer(make_client):
    c = make_client()
    c.post("/ingest/paste", json={"title": "Resume", "text": RESUME})
    original = c.app.state.agent.provider.generate_structured

    async def critic(stage, context, schema):
        if stage == "verify":
            return VerificationResult(
                unsupported_claims=["Candidate year is not established"],
                confidence=0,
                should_continue_research=False,
                justification="Reject unsupported claim",
            )
        return await original(stage, context, schema)

    c.app.state.agent.provider.generate_structured = critic
    _, result = run(c, "What year did Abdul graduate?")
    assert result["status"] == "insufficient_information" and result["confidence"] == 0
    assert result["final_answer"] is None and result["final_response"]["answer"] is None
    assert "Candidate year is not established" in result["uncertainty"]


def test_conflicting_facts_keep_provenance_and_freshness(make_client):
    c = make_client()
    harness = c.app.state.agent.harness
    for value, stamp in [
        ("B99 1st floor", "2025-01-01T00:00:00+00:00"),
        ("B99 2nd floor", datetime.now(timezone.utc).isoformat()),
    ]:
        fact = MemoryFact(
            subject="Jake Peralta",
            predicate="current_site",
            value=value,
            source="human_confirmed",
            source_system="human_input",
            observed_at=stamp,
            confidence=1,
            case_id="prior-" + value,
        )
        c.portal.call(harness.invoke, "remember_fact", {"fact": fact.model_dump()})
    _, result = run(c, "Jake Peralta from B99 cannot connect to VPN.")
    assert result["contradictions"]
    assert (
        "2025" in result["contradictions"][0]
        and "Evaluate freshness" in result["contradictions"][0]
    )
    assert len(result["remembered_facts"]) == 2


@pytest.mark.asyncio
async def test_mcp_inventory_is_typed_and_graph_unavailable_is_explicit(settings):
    client = DeskMateMCPClient(settings)
    try:
        descriptors = await client.list_tools()
        expected = {
            "search_graph",
            "get_ticket",
            "ingest_text",
            "ingest_document",
            "search_memory",
            "get_case_history",
            "list_available_context",
        }
        assert expected <= {d["name"] for d in descriptors}
        for item in descriptors:
            assert item["description"] and "properties" in item["inputSchema"]
            assert {"tool", "source_system", "timestamp", "status", "items"} <= set(
                item["outputSchema"]["properties"]
            )
        result = await client.call_tool("search_graph", {"entities": ["Jake Peralta"]})
        assert result["status"] == "unavailable" and result["result"]["available"] is False
    finally:
        await client.close()


def test_failed_subgraph_plan_retries_checkpoint_without_reanalyzing(make_client):
    from backend.app.models.provider import LLMError

    c = make_client()
    original = c.app.state.agent.provider.generate_structured
    calls = 0

    async def once(stage, context, schema):
        nonlocal calls
        if stage == "plan":
            calls += 1
            if calls == 1:
                raise LLMError("Temporary planning failure")
        return await original(stage, context, schema)

    c.app.state.agent.provider.generate_structured = once
    cid = c.post("/cases", json={"summary": "Jake Peralta from B99 cannot connect to VPN."}).json()[
        "case_id"
    ]
    assert c.post(f"/cases/{cid}/investigate").status_code == 502
    result = c.post(f"/cases/{cid}/investigate")
    assert result.status_code == 200 and result.json()["status"] == "needs_input"
    events = c.get(f"/cases/{cid}/events").json()
    assert sum(e["tool"] == "analyze_ticket" for e in events) == 1
    assert sum(e["tool"] == "human_input_requested" for e in events) == 1
    c.post(f"/cases/{cid}/resume", json={"answer": "2nd floor"})
    events = c.get(f"/cases/{cid}/events").json()
    assert sum(e["tool"] == "human_input_requested" for e in events) == 2


def test_critic_revises_preliminary_causation_and_material_question(make_client):
    from backend.app.agent.models import InvestigationDecision

    c = make_client()
    original = c.app.state.agent.provider.generate_structured
    reasons = critics = 0

    async def generated(stage, context, schema):
        nonlocal reasons, critics
        if stage == "reason":
            reasons += 1
            if reasons == 1:
                return InvestigationDecision(
                    hypotheses=[
                        {
                            "claim": "The change caused the failure",
                            "status": "supported",
                            "confidence": 0.9,
                        }
                    ],
                    evidence_summary="Premature cause",
                    question_for_human="Was the update installed?",
                    human_field="required_update_installed",
                    question_reason="An update is mentioned",
                    enough_information=False,
                    confidence=0.9,
                )
        if stage == "verify":
            critics += 1
            assert "verification" not in context and "analysis" not in context
            assert "decision_issues" not in context
            if critics == 1:
                return VerificationResult(
                    unsupported_claims=["Causation and current site are unconfirmed"],
                    missing_evidence=[
                        "Ask the current site before claiming the site-specific alert applies"
                    ],
                    should_continue_research=True,
                    confidence=0.2,
                    justification="Revise the premature hypothesis and pending question",
                )
        return await original(stage, context, schema)

    c.app.state.agent.provider.generate_structured = generated
    _, result = run(c, "Jake Peralta from B99 cannot connect to VPN.")
    assert result["status"] == "needs_input" and "floor" in result["pending_question"]
    assert reasons == 2 and critics == 2
    assert result["hypotheses"][0]["status"] == "plausible"
    assert "not yet confirmed" in result["hypotheses"][0]["claim"]


def test_short_user_name_loads_context_before_material_site_question(make_client):
    c = make_client()
    cid, result = run(c, "Jake cannot connect to VPN")
    assert result["workflow"] == "service_desk" and result["status"] == "needs_input"
    assert "exact error message" in result["pending_question"]
    tools = [e["tool"] for e in c.get(f"/cases/{cid}/events").json()]
    assert tools.count("get_user_context") == 1
    assert "get_active_alerts" not in tools


def test_shared_outage_clarifies_company_then_checks_shared_service(make_client):
    c = make_client()
    cid, result = run(c, "Five users at the same company report VPN failure.")
    assert result["analysis"]["affected_scope"] == "multiple users"
    assert result["pending_question"] == "Which company is affected?"
    final = c.post(f"/cases/{cid}/resume", json={"answer": "B99"})
    assert final.status_code == 200 and final.json()["status"] == "assessment_ready"
    tools = [e["tool"] for e in c.get(f"/cases/{cid}/events").json()]
    assert "get_active_alerts" in tools and "get_recent_changes" in tools
    assert "get_device_context" not in tools


def test_unjustified_mfa_location_question_is_revised_before_interrupt(make_client):
    from backend.app.agent.models import InvestigationDecision

    c = make_client()
    original = c.app.state.agent.provider.generate_structured
    count = 0

    async def generated(stage, context, schema):
        nonlocal count
        if stage == "reason":
            count += 1
            if count == 1:
                return InvestigationDecision(
                    evidence_summary="Location is unknown",
                    question_for_human="Which floor is Sarah on?",
                    human_field="current_site",
                    question_reason="User location absent",
                    enough_information=False,
                    confidence=0.5,
                )
            assert context["decision_issues"]
            assert "verification" not in context
        return await original(stage, context, schema)

    c.app.state.agent.provider.generate_structured = generated
    cid, result = run(c, "Sarah cannot complete Microsoft 365 MFA.")
    assert result["status"] == "assessment_ready" and result["pending_question"] is None
    assert count == 2
    events = c.get(f"/cases/{cid}/events").json()
    assert not any(e["tool"] == "human_input_requested" for e in events)
def test_final_answer_is_one_short_plain_line():
    assert concise_answer_line("**Check the logs.**\n\nThen retry later.") == "Check the logs. Then retry later."
    assert "\n" not in concise_answer_line("Review evidence\nfrom the verified source")
