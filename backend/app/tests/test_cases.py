import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app

TICKET = "Jake Peralta from B99 cannot connect to the VPN. Authentication failed."
SITE_QUESTION = "Which site or floor is Jake Peralta currently working from?"
UPDATE_QUESTION = "Has Jake Peralta installed the required VPN client update?"


def create(client):
    response = client.post("/cases", json={"summary": TICKET})
    assert response.status_code == 201
    return response.json()["case_id"]


def test_full_case_interrupt_resume_events_and_cross_case_memory(make_client):
    c = make_client()
    cid = create(c)
    first = c.post(f"/cases/{cid}/investigate").json()
    assert first["status"] == "needs_input" and first["pending_question"] == SITE_QUESTION
    agent = c.app.state.agent
    snapshot = c.portal.call(agent.graph.aget_state, agent.config(cid))
    assert snapshot.next == ("ask_human",)
    assert snapshot.tasks[0].interrupts[0].value["question"] == SITE_QUESTION
    assert snapshot.config["configurable"]["thread_id"] == cid
    second = c.post(f"/cases/{cid}/resume", json={"answer": "2nd floor"}).json()
    assert second["case_id"] == cid and second["thread_id"] == cid
    assert second["status"] == "needs_input" and second["pending_question"] == UPDATE_QUESTION
    assert second["confirmed_facts"][0]["value"] == "B99 2nd floor"
    final = c.post(f"/cases/{cid}/resume", json={"answer": "Yes"}).json()
    assert final["status"] == "assessment_ready"
    assert len(final["confirmed_facts"]) == 2
    assert "profile" in final["recommended_next_action"]
    assert final["uncertainty"]
    evidence = final["evidence"]
    for name in ("get_active_alerts", "get_recent_changes", "search_previous_tickets"):
        assert any(e["tool"] == name and "B99 2nd floor" in str(e["content"]) for e in evidence)
    events = c.get(f"/cases/{cid}/events").json()
    assert sum(e["tool"] == "analyze_ticket" for e in events) == 1
    # Passing verification finalizes immediately; trusted context persistence
    # happens inside finalization without another reasoning cycle.
    assert sum(e["tool"] == "remember_fact" for e in events) == 3
    assert agent.harness.invocation_count > 10
    other = create(c)
    later = c.post(f"/cases/{other}/investigate").json()
    assert other != cid and later["status"] == "assessment_ready"
    assert {f["predicate"] for f in later["remembered_facts"]} == {
        "current_site",
        "required_update_installed",
        "works_at",
    }
    assert all(f["case_id"] == cid for f in later["remembered_facts"])
    assert not later["pending_question"]
    assert len(c.get("/cases").json()) == 2
    assert c.post(f"/cases/{cid}/resume", json={"answer": "again"}).status_code == 409


def test_interrupt_survives_application_restart(settings):
    with TestClient(create_app(settings)) as c:
        cid = create(c)
        assert c.post(f"/cases/{cid}/investigate").json()["pending_question"] == SITE_QUESTION
    with TestClient(create_app(settings)) as c:
        assert c.get(f"/cases/{cid}").json()["pending_question"] == SITE_QUESTION
        resumed = c.post(f"/cases/{cid}/resume", json={"answer": "2nd floor"})
        assert resumed.status_code == 200 and resumed.json()["pending_question"] == UPDATE_QUESTION
    with TestClient(create_app(settings)) as c:
        final = c.post(f"/cases/{cid}/resume", json={"answer": "Yes"}).json()
        assert final["status"] == "assessment_ready"
        events = c.get(f"/cases/{cid}/events").json()
        assert sum(e["tool"] == "analyze_ticket" for e in events) == 1


def test_first_start_creates_checkpoint_directory(settings):
    nested = settings.model_copy(update={"case_db_path": settings.case_db_path.parent / "new" / "nested" / "cases.db"})
    assert not nested.case_db_path.parent.exists()
    with TestClient(create_app(nested)) as client:
        assert client.get("/cases").status_code == 200
        assert nested.case_db_path.parent.exists()


def test_missing_case_and_validation(make_client):
    c = make_client()
    for route in ("/cases/missing", "/cases/missing/events"):
        assert c.get(route).status_code == 404
    for route in ("investigate", "resume"):
        assert c.post(f"/cases/missing/{route}", json={"answer": "Yes"}).status_code == 404
    assert c.post("/cases", json={"summary": ""}).status_code == 422
    assert c.post("/cases", json={"summary": "a" * 20001}).status_code == 422
    cid = create(c)
    assert c.post(f"/cases/{cid}/resume", json={"answer": "Yes"}).status_code == 409


@pytest.mark.parametrize("stage", ["analyze_ticket", "reason"])
def test_llm_failure_is_explicit_and_safe(make_client, stage):
    from backend.app.models.provider import LLMError

    c = make_client()
    provider = c.app.state.agent.provider
    original = provider.generate_structured

    async def fail(current_stage, context, schema):
        if current_stage == stage:
            raise LLMError("Invalid structured model output")
        return await original(current_stage, context, schema)

    provider.generate_structured = fail
    cid = create(c)
    response = c.post(f"/cases/{cid}/investigate")
    assert response.status_code == 502
    assert response.json()["detail"] == "Invalid structured model output"
    assert c.get(f"/cases/{cid}").json()["status"] == "error"


def test_historical_case_read_is_safe_without_rewriting_saved_data(make_client):
    c = make_client()
    old = {
        "case_id": "historical",
        "ticket": {"summary": "Historical incident"},
        "status": "resolved",
        "pending_question": "An unsupported historical question",
        "confidence": 0.82,
        "evidence": [],
    }
    from backend.app.agent.memory import CaseMemoryStore
    memory = CaseMemoryStore(c.app.state.settings.case_db_path)
    memory.save_case("historical", old)
    result = c.get("/cases/historical")
    assert result.status_code == 200
    assert result.json()["status"] == "insufficient_information"
    assert result.json()["confidence"] == 0 and result.json()["pending_question"] is None
    assert memory.load_case("historical") == old
    assert c.get("/cases").status_code == 200


def test_explicit_service_failure_can_retry_the_saved_node(make_client):
    from backend.app.models.provider import LLMError

    c = make_client()
    provider = c.app.state.agent.provider
    original = provider.generate_structured
    attempts = 0

    async def once(stage, context, schema):
        nonlocal attempts
        if stage == "reason":
            attempts += 1
            if attempts == 1:
                raise LLMError("previous hosted provider rate limit reached")
        return await original(stage, context, schema)

    provider.generate_structured = once
    cid = create(c)
    assert c.post(f"/cases/{cid}/investigate").status_code == 502
    retry = c.post(f"/cases/{cid}/investigate")
    assert retry.status_code == 200 and retry.json()["pending_question"] == SITE_QUESTION
    assert retry.json()["thread_id"] == cid
    events = c.get(f"/cases/{cid}/events").json()
    assert sum(e["tool"] == "analyze_ticket" for e in events) == 1


def test_history_provenance_and_resolution_preserve_existing_records():
    from backend.app.main import case_response

    state = {"case_id": "kept", "ticket": {"summary": "Fictional VPN acceptance"}, "status": "new"}
    assert case_response(state).origin == "demo"
    assert "origin" not in state
    assert case_response({**state, "origin": "user"}).origin == "user"
    customer = {**state, "ticket": {"summary": "A customer VPN issue"}}
    assert case_response(customer).origin == "unknown"
    assert case_response({**state, "ticket": {"summary": "Fictional test: VPN"}}).origin == "test"
    ready = case_response({**state, "status": "assessment_ready"})
    assert ready.resolution_status == "unconfirmed"
    resolved = case_response({
        **state, "voice_intake": {"resolution_status": "user_confirmed_resolved"}
    })
    assert resolved.resolution_status == "user_confirmed_resolved"


def test_explicit_case_origin_survives_investigation(make_client):
    client = make_client()
    created = client.post("/cases", json={"summary": TICKET}).json()
    assert created["origin"] == "test"
    investigated = client.post(f"/cases/{created['case_id']}/investigate").json()
    assert investigated["origin"] == "test"
    assert client.get(f"/cases/{created['case_id']}").json()["origin"] == "test"
