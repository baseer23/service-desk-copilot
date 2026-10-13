import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from backend.app.core.config import Settings
from backend.app.testing.provider_stub import StubProvider
from backend.app.voice.auth import Actor, VoiceError, issue_actor, verify_actor
from backend.app.voice.graph import VoiceSupportGraph
from backend.app.voice.models import (
    ContactCandidate,
    TranscriptTurn,
    VoiceConversation,
    VoiceExtraction,
    VoicePlan,
    VoiceReply,
    VoiceStatus,
)
from backend.app.voice.normalization import normalize_email, normalize_name, normalize_phone
from backend.app.voice.repository import SQLiteVoiceConversationRepository
from pydantic import SecretStr, ValidationError

ORIGIN = {"Origin": "http://testserver"}
ACTOR = Actor("owner", "local-demo")


@pytest.fixture
def voice_client(make_client):
    return make_client(
        {
            "VOICE_ENABLED": "true",
            "VOICE_PROVIDER": "fake",
            "VOICE_SESSION_SIGNING_KEY": "isolated-test-signing-key-xxxxxxxxxxxxxxxxxxxx",
        }
    )


def start(client):
    response = client.post("/voice/sessions", json={}, headers=ORIGIN)
    assert response.status_code == 201, response.text
    return response.json()["session_id"]


def receive_until(socket, kind):
    for _ in range(30):
        event = socket.receive_json()
        if event["type"] == kind:
            return event
    raise AssertionError(kind)


def send(socket, sid, seq, kind, **payload):
    socket.send_json(
        {"version": 1, "type": kind, "session_id": sid, "sequence": seq, "payload": payload}
    )


def test_start_owner_security_secrets_and_stop(voice_client):
    c = voice_client
    assert c.post("/voice/sessions", json={}).status_code == 403
    assert (
        c.post("/voice/sessions", json={}, headers={"Origin": "https://evil.invalid"}).status_code
        == 403
    )
    sid = start(c)
    body = c.get("/voice/sessions/" + sid).json()
    assert "owner_id" not in body and "provider_session_id" not in body
    assert "signing" not in json.dumps(body)
    assert c.post("/voice/sessions", json={}, headers=ORIGIN).status_code == 409
    saved = c.cookies.get("deskmate_voice_identity")
    c.cookies.clear()
    assert c.get("/voice/sessions/" + sid).status_code == 401
    c.cookies.set(
        "deskmate_voice_identity", issue_actor(c.app.state.settings, "someone-else"), path="/voice"
    )
    assert c.get("/voice/sessions/" + sid).status_code == 404
    c.cookies.clear()
    c.cookies.set("deskmate_voice_identity", saved, path="/voice")
    assert c.post("/voice/sessions/" + sid + "/stop", headers=ORIGIN).json()["status"] == "stopped"
    assert c.get("/health").json()["voice_provider"] == "fake"


def test_websocket_fake_text_audio_duplicate_stop_cleanup(voice_client):
    c = voice_client
    sid = start(c)
    with c.websocket_connect("/voice/sessions/" + sid + "/stream", headers=ORIGIN) as ws:
        snapshot = receive_until(ws, "session.snapshot")
        assert snapshot["version"] == 1
        greeting = receive_until(ws, "transcript.assistant.final")
        assert "AI support" in greeting["payload"]["text"]
        receive_until(ws, "audio")
        receive_until(ws, "session.snapshot")
        send(ws, sid, 1, "text.input", text="My name is Alex Test")
        receive_until(ws, "transcript.user.final")
        response = receive_until(ws, "transcript.assistant.final")
        assert (
            "Alex Test" in response["payload"]["text"]
            and response["payload"]["text"].count("?") == 1
        )
        receive_until(ws, "audio")
        receive_until(ws, "session.snapshot")
        send(ws, sid, 1, "text.input", text="DUPLICATE PRIVATE")
        send(ws, sid, 2, "text.input", text="yes")
        receive_until(ws, "transcript.user.final")
        receive_until(ws, "transcript.assistant.final")
        receive_until(ws, "audio")
        state = receive_until(ws, "session.snapshot")["payload"]["conversation"]
        assert state["confirmed_profile"]["name"] == "Alex Test"
        assert all("DUPLICATE" not in turn["text"] for turn in state["turns"])
        send(ws, sid, 3, "session.stop")
    assert c.get("/voice/sessions/" + sid).json()["status"] == "stopped"
    assert not c.app.state.voice.active


def test_out_of_order_malformed_and_disconnect(voice_client):
    c = voice_client
    sid = start(c)
    with c.websocket_connect("/voice/sessions/" + sid + "/stream", headers=ORIGIN) as ws:
        receive_until(ws, "session.snapshot")
        send(ws, sid, 4, "ping")
        assert "Reconnect" in receive_until(ws, "error")["payload"]["message"]
    assert c.get("/voice/sessions/" + sid).json()["status"] == "failed"
    with c.websocket_connect("/voice/sessions/" + sid + "/stream", headers=ORIGIN) as ws:
        receive_until(ws, "session.snapshot")
    # HTTP polling may race teardown after the browser disconnects.
    for _ in range(100):
        if c.get("/voice/sessions/" + sid).json()["failure_category"] == "client_disconnected":
            break
        import time

        time.sleep(0.01)
    assert c.get("/voice/sessions/" + sid).json()["failure_category"] == "client_disconnected"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("alex underscore test at example dot com", "alex_test@example.com"),
        ("ALEX@test.example", "alex@test.example"),
    ],
)
def test_email_normalizes(raw, expected):
    assert normalize_email(raw) == expected


@pytest.mark.parametrize(
    "raw", ["alex", "alex@", "alex@example", "a..b@example.com", "alex at invented", "@example.com"]
)
def test_email_rejects(raw):
    with pytest.raises(ValueError):
        normalize_email(raw)


def test_numbers_use_library_and_region():
    assert (
        normalize_phone("plus one two zero two five five five zero one two three") == "+12025550123"
    )
    assert normalize_phone("2025550123", "US") == "+12025550123"
    with pytest.raises(ValueError, match="country"):
        normalize_phone("2025550123")
    with pytest.raises(ValueError):
        normalize_phone("+15555550123")
    assert normalize_name("  Alex Test  ") == "Alex Test"


class Harness:
    def __init__(self, items=None, error=False):
        self.items = items or []
        self.error = error
        self.calls = []

    def schemas(self, workflow):
        return []

    def validate_plan(self, plan, workflow):
        from backend.app.agent.tools import READ_TOOLS

        if any(c.name not in READ_TOOLS for c in plan.tool_calls):
            raise ValueError("Write denied")

    async def invoke(self, name, args):
        self.calls.append(name)
        if self.error:
            raise RuntimeError("private body")
        return {
            "status": "ok",
            "items": self.items,
            "source_system": "local_fixture",
            "timestamp": "2026-10-12T00:00:00+00:00",
            "reliability": 0.9,
        }


@pytest.mark.asyncio
async def test_contacts_correction_confirmations_bounded(settings):
    graph = VoiceSupportGraph(settings, Harness(), StubProvider())
    state = VoiceConversation(session_id="a" * 32, owner_id="owner", tenant_id="local-demo")
    for i, text in enumerate(
        [
            "My name is Alex Test",
            "no",
            "My name is Alexandra Test",
            "yes",
            "Email is alex at example dot com",
            "no",
            "Email is alexandra at example dot com",
            "yes",
            "Callback: plus one two zero two five five five zero one two three",
            "yes",
        ]
    ):
        state, reply = await graph.run(state, text, str(i))
        assert reply.count("?") <= 1
    assert state.confirmed_profile == {
        "name": "Alexandra Test",
        "email": "alexandra@example.com",
        "callback_number": "+12025550123",
    }
    state, reply = await graph.run(state, "Email is new at example dot com", "change")
    assert (
        "email" not in state.confirmed_profile
        and state.pending_confirmation.value == "new@example.com"
    )
    for i in range(5):
        state, reply = await graph.run(state, "maybe", str(i))
    assert "email" in state.manual_contact_fields and "correct?" not in reply


@pytest.mark.asyncio
async def test_real_mcp_evidence_grounding_result_and_failure(settings):
    from backend.app.agent.tools import AgentToolHarness
    from backend.app.mcp.client import DeskMateMCPClient

    mcp = DeskMateMCPClient(settings)
    try:
        await mcp.start()
        h = AgentToolHarness(mcp)
        await h.initialize()
        await h.invoke(
            "ingest_text",
            {
                "title": "VPN KB",
                "text": "If VPN is broken, check whether the VPN client shows connected.",
            },
        )
        state = VoiceConversation(
            session_id="b" * 32,
            owner_id="owner",
            tenant_id="local-demo",
            confirmed_profile={
                "name": "Alex",
                "email": "a@example.com",
                "callback_number": "+12025550123",
            },
        )
        graph = VoiceSupportGraph(settings, h, StubProvider())
        state, reply = await graph.run(state, "My VPN is broken", "turn1")
        assert state.evidence and state.actions and "Did that work?" in reply
        assert state.evidence[0].tool == "search_knowledge"
        state, reply = await graph.run(state, "it worked", "turn2")
        assert (
            state.resolution_status == "user_confirmed_resolved"
            and state.status == VoiceStatus.COMPLETED
        )
    finally:
        await mcp.close()
    graph = VoiceSupportGraph(settings, Harness(error=True), StubProvider())
    state = VoiceConversation(
        session_id="c" * 32,
        owner_id="owner",
        tenant_id="local-demo",
        confirmed_profile={
            "name": "Alex",
            "email": "a@example.com",
            "callback_number": "+12025550123",
        },
    )
    state, reply = await graph.run(state, "VPN is broken", "t")
    assert state.resolution_status == "escalation_required" and "private" not in reply


@pytest.mark.asyncio
async def test_no_invented_contact_or_write_action(settings):
    class Bad:
        async def generate_structured(self, stage, context, schema):
            if stage == "voice_extract":
                return VoiceExtraction(
                    profile={
                        "email": ContactCandidate(value="invented@example.com", raw_quote="Alex")
                    }
                )
            if stage == "voice_plan":
                from backend.app.agent.models import ToolRequest

                return VoicePlan(tool_calls=[ToolRequest(name="save_case")])

    graph = VoiceSupportGraph(
        settings.model_copy(update={"knowledge_scope": "broad"}), Harness(), Bad()
    )
    state = VoiceConversation(session_id="d" * 32, owner_id="owner", tenant_id="local-demo")
    with pytest.raises(ValueError, match="Write denied"):
        await graph.run(state, "Alex", "t")
    assert not state.confirmed_profile


def test_repository_lease_replay_transaction_retention_and_case(settings):
    from backend.app.agent.memory import CaseMemoryStore

    # Repository mutations use the same atomic transaction as case insertion.
    repo = SQLiteVoiceConversationRepository(settings.case_db_path)
    c = repo.create(ACTOR)
    repo.claim(c.session_id, ACTOR, "first")
    with pytest.raises(VoiceError):
        repo.claim(c.session_id, ACTOR, "second")

    def add(state):
        state.turns.append(TranscriptTurn(id="t", role="user", text="private@example.com"))

    repo.update(c.session_id, ACTOR, add, "first", "event")
    repo.update(c.session_id, ACTOR, add, "first", "event")
    assert len(repo.get(c.session_id, ACTOR).turns) == 1
    with pytest.raises(VoiceError):
        repo.update(c.session_id, ACTOR, add, "stale")

    def fail(state):
        state.issue_summary = "changed"
        raise ValueError("rollback")

    with pytest.raises(ValueError):
        repo.update(c.session_id, ACTOR, fail)
    assert not repo.get(c.session_id, ACTOR).issue_summary
    assert "private" not in json.dumps(repo.events(c.session_id, ACTOR))
    store = CaseMemoryStore(settings.case_db_path)
    with pytest.raises(VoiceError):
        repo.create_case(c.session_id, ACTOR, store)

    def confirm(state):
        state.confirmed_profile = {
            "name": "Alex",
            "email": "a@example.com",
            "callback_number": "+12025550123",
        }
        state.issue_summary = "VPN broken"
        state.status = VoiceStatus.STOPPED

    repo.update(c.session_id, ACTOR, confirm)
    case_id = repo.create_case(c.session_id, ACTOR, store)
    assert repo.create_case(c.session_id, ACTOR, store) == case_id
    assert store.load_case(case_id)["voice_intake"]["resolution_status"] == "unconfirmed"
    repo.purge(ACTOR.tenant_id, "9999")
    assert not repo.get(c.session_id, ACTOR).turns
    with pytest.raises(VoiceError):
        repo.get(c.session_id, Actor("other", ACTOR.tenant_id))


@pytest.mark.parametrize(
    "field,value",
    [("generation_provider", "other"), ("embed_provider", "sentence"), ("voice_provider", "fake")],
)
def test_non_azure_runtime_rejected(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, test_mode=False, **{field: value})


def test_identity_tamper_expiry_and_production(settings):
    s = settings.model_copy(
        update={
            "voice_session_signing_key": SecretStr("test-secret-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        }
    )
    token = issue_actor(s, "owner")
    assert verify_actor(s, token).owner_id == "owner"
    with pytest.raises(VoiceError):
        verify_actor(s, token + "a")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, deployment_mode="production", azure_use_entra_id=False)


@pytest.mark.asyncio
async def test_runtime_partial_transcripts_barge_in_late_audio_and_fidelity(settings):
    from backend.app.testing.voice import FakeVoiceLiveProvider
    from backend.app.voice.service import VoiceManager, VoiceRuntime

    fake = FakeVoiceLiveProvider()
    manager = VoiceManager(settings, Harness(), StubProvider(), provider_factory=lambda: fake)
    sid = manager.repository.create(ACTOR).session_id
    runtime = VoiceRuntime(manager, AsyncMock(), sid, ACTOR)
    manager.repository.claim(sid, ACTOR, runtime.lease)
    runtime.approved = "What error appears?"
    runtime.pending_response = True
    runtime.created_epochs.append(0)
    runtime.assistant_id = "a"
    manager.repository.update(
        sid,
        ACTOR,
        lambda c: c.turns.append(TranscriptTurn(id="a", role="assistant", text=runtime.approved)),
    )
    receive = asyncio.create_task(runtime.receiver())
    await fake.emit("response.created", response_id="r1")
    await fake.emit("assistant.delta", "What ", response_id="r1")
    await fake.emit("assistant.delta", "error appears?", response_id="r1")
    await fake.emit("speech.started")
    await fake.emit("audio", audio="AAA=", response_id="r1")
    await fake.emit("user.delta", "VPN ", item_id="u")
    await fake.emit("user.final", "VPN broken", item_id="u")
    await fake.emit("user.final", "VPN broken", item_id="u")
    await asyncio.sleep(0.02)
    assert ("cancel", "") in fake.actions
    output = []
    while not runtime.outgoing.empty():
        output.append(runtime.outgoing.get_nowait())
    assert not any(kind == "audio" for kind, _ in output)
    assert [p["text"] for kind, p in output if kind == "transcript.assistant.partial"] == [
        "What ",
        "error appears?",
    ]
    assert sum(t.id == "u" for t in runtime.state().turns) == 1
    assert runtime.state().turns[0].state == "interrupted"
    # An unsolicited/changed speech reply cannot become a verified answer.
    runtime.created_epochs.append(runtime.epoch)
    runtime.pending_response = True
    await fake.emit("response.created", response_id="r2")
    await fake.emit("assistant.final", "Your issue is resolved", response_id="r2")
    with pytest.raises(VoiceError, match="diverged"):
        await receive
    assert runtime.state().resolution_status == "unconfirmed"
    await fake.close_session()


@pytest.mark.asyncio
async def test_pending_response_cancel_before_created_and_audio_queue_order(settings):
    from backend.app.testing.voice import FakeVoiceLiveProvider
    from backend.app.voice.service import VoiceManager, VoiceRuntime

    fake = FakeVoiceLiveProvider()
    manager = VoiceManager(settings, Harness(), StubProvider(), provider_factory=lambda: fake)
    sid = manager.repository.create(ACTOR).session_id
    runtime = VoiceRuntime(manager, AsyncMock(), sid, ACTOR)
    manager.repository.claim(sid, ACTOR, runtime.lease)
    runtime.pending_response = True
    runtime.created_epochs.append(runtime.epoch)
    await runtime.interrupt()
    receive = asyncio.create_task(runtime.receiver())
    await fake.emit("response.created", response_id="late")
    await fake.emit("audio", audio="AAA=", response_id="late")
    await asyncio.sleep(0.01)
    assert "late" in runtime.cancelled and ("cancel", "") in fake.actions
    receive.cancel()
    await asyncio.gather(receive, return_exceptions=True)
    await fake.close_session()


@pytest.mark.asyncio
async def test_real_mcp_voice_identity_and_write_permission(settings):
    from backend.app.agent.models import InvestigationPlan, ToolRequest
    from backend.app.agent.tools import AgentToolHarness
    from backend.app.mcp.client import DeskMateMCPClient

    settings = settings.model_copy(
        update={
            "voice_session_signing_key": SecretStr("mcp-test-signing-key-xxxxxxxxxxxxxxxxxxxxxxxx")
        }
    )
    repo = SQLiteVoiceConversationRepository(settings.case_db_path)
    state = repo.create(ACTOR)

    def confirm(c):
        c.confirmed_profile = {
            "name": "Alex",
            "email": "a@example.com",
            "callback_number": "+12025550123",
        }
        c.issue_summary = "VPN issue"

    repo.update(state.session_id, ACTOR, confirm)
    mcp = DeskMateMCPClient(settings)
    try:
        await mcp.start()
        h = AgentToolHarness(mcp)
        await h.initialize()
        token = issue_actor(settings, ACTOR.owner_id)
        own = await h.invoke(
            "get_voice_session", {"session_id": state.session_id, "identity_token": token}
        )
        assert own["status"] == "ok" and own["result"]["confirmed_profile"]["name"] == "Alex"
        from backend.app.mcp.client import MCPToolError

        with pytest.raises(MCPToolError) as rejected:
            await h.invoke(
                "get_voice_session",
                {"session_id": state.session_id, "identity_token": issue_actor(settings, "other")},
            )
        assert rejected.value.status_code == 404
        plan = InvestigationPlan(
            goal="Write",
            tool_calls=[
                ToolRequest(
                    name="create_case_from_voice_session",
                    arguments={"session_id": state.session_id, "identity_token": token},
                )
            ],
            stop_condition="done",
        )
        with pytest.raises(ValueError, match="not permitted"):
            h.validate_plan(plan, "voice")
        result = await h.invoke(
            "create_case_from_voice_session",
            {"session_id": state.session_id, "identity_token": token},
        )
        assert result["result"]["case_id"] == "voice-" + state.session_id
    finally:
        await mcp.close()


def test_no_removed_provider_references_in_current_source():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    forbidden = "g" + "roq"
    paths = [root / ".env.example", root / "AGENTS.md"]
    for directory in ["backend", "frontend/src", "docs", "templates", "scripts"]:
        paths.extend(
            path
            for path in (root / directory).rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix in {".py", ".md", ".ts", ".tsx", ".env", ".sh"}
        )
    assert not [
        str(path.relative_to(root)) for path in paths if forbidden in path.read_text().casefold()
    ]


def test_cannot_resume_old_session_while_new_session_is_active(settings):
    repo = SQLiteVoiceConversationRepository(settings.case_db_path)
    old = repo.create(ACTOR)
    repo.update(old.session_id, ACTOR, lambda c: setattr(c, "status", VoiceStatus.FAILED))
    new = repo.create(ACTOR)
    with pytest.raises(VoiceError, match="other voice conversation"):
        repo.claim(old.session_id, ACTOR, "old-lease")
    repo.claim(new.session_id, ACTOR, "new-lease")
    repo.update(new.session_id, ACTOR, lambda c: setattr(c, "status", VoiceStatus.STOPPED))
    with pytest.raises(VoiceError, match="closing"):
        repo.create(ACTOR)
    repo.release(new.session_id, ACTOR, "new-lease")
    assert repo.create(ACTOR).session_id


def test_voice_case_summary_and_events_remain_owner_scoped(voice_client):
    c = voice_client
    sid = start(c)
    settings = c.app.state.settings
    actor = verify_actor(settings, c.cookies.get("deskmate_voice_identity"))

    def confirm(state):
        state.confirmed_profile = {
            "name": "Alex",
            "email": "private@example.com",
            "callback_number": "+12025550123",
        }
        state.issue_summary = "Private VPN issue"

    c.app.state.voice.repository.update(sid, actor, confirm)
    response = c.post("/voice/sessions/" + sid + "/create-case", headers=ORIGIN)
    assert response.status_code == 200, response.text
    cid = response.json()["case_id"]
    assert c.get("/cases/" + cid).status_code == 200
    c.cookies.clear()
    assert c.get("/cases/" + cid).status_code == 401
    assert c.get("/cases/" + cid + "/events").status_code == 401
    assert (
        c.post("/requests/" + cid + "/resume", headers=ORIGIN, json={"answer": "yes"}).status_code
        == 401
    )
    assert not c.get("/cases").json()
    c.cookies.set("deskmate_voice_identity", issue_actor(settings, "other"), path="/")
    assert c.get("/cases/" + cid).status_code == 404


@pytest.mark.asyncio
async def test_rejected_ungrounded_elevated_and_repeated_actions(settings):
    from backend.app.voice.models import TroubleshootingAction

    class Model:
        def __init__(self, action):
            self.action = action

        async def generate_structured(self, stage, context, schema):
            if stage == "voice_extract":
                return VoiceExtraction()
            if stage == "voice_plan":
                return VoicePlan()
            return VoiceReply(reply="Unverified", action=self.action, evidence_ids=["invented"])

    for action in ["Delete the registry", "Check a service", "Repeat a failed step"]:
        c = VoiceConversation(
            session_id="e" * 32,
            owner_id="owner",
            tenant_id="local-demo",
            issue_summary="VPN broken",
        )
        c.actions = [
            TroubleshootingAction(id="a", instruction=action, evidence_ids=[], result="failed")
        ]
        state, reply = await VoiceSupportGraph(
            settings.model_copy(update={"knowledge_scope": "broad"}), Harness(), Model(action)
        ).run(c, "still broken", "t")
        assert state.resolution_status == "escalation_required" and len(state.actions) == 1
        assert "escalating" in reply


@pytest.mark.asyncio
async def test_provider_and_tool_deadlines_are_explicit(settings):
    from backend.app.voice.service import VoiceManager, VoiceRuntime

    fake = AsyncMock()
    fake.create_session.side_effect = RuntimeError("private.azure-key")
    manager = VoiceManager(settings, Harness(), StubProvider(), provider_factory=lambda: fake)
    sid = manager.repository.create(ACTOR).session_id
    socket = AsyncMock()
    runtime = VoiceRuntime(manager, socket, sid, ACTOR)
    await runtime.run()
    assert runtime.state().failure_category == "connection_failed"
    fake.close_session.assert_awaited_once()
    assert "private" not in str(socket.send_json.call_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_live_greeting_finishes_before_listening_and_stop_cancels_preparation(
    settings, cancel
):
    from backend.app.testing.voice import FakeVoiceLiveProvider
    from backend.app.voice.service import VoiceManager, VoiceRuntime

    fake = FakeVoiceLiveProvider()
    manager = VoiceManager(
        settings.model_copy(update={"test_mode": False}),
        Harness(),
        StubProvider(),
        provider_factory=lambda: fake,
    )
    sid = manager.repository.create(ACTOR).session_id
    socket = AsyncMock()
    never = asyncio.Event()
    socket.receive_text.side_effect = never.wait
    runtime = VoiceRuntime(manager, socket, sid, ACTOR)
    started, release, rendered = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def greeting():
        started.set()
        await release.wait()
        return "Generated opening question"

    original = fake.respond

    async def respond(text):
        assert text == "Generated opening question"
        await original(text)
        rendered.set()

    runtime.graph.greeting = greeting
    fake.respond = respond
    task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(started.wait(), 2)
    assert runtime.state().status == VoiceStatus.CONNECTING
    assert not socket.send_json.called and not fake.actions
    if not cancel:
        release.set()
        await asyncio.wait_for(rendered.wait(), 2)
        assert any(action == "respond" for action, _ in fake.actions)
    manager.end(sid, ACTOR)
    await asyncio.wait_for(task, 2)
    assert runtime.state().status == VoiceStatus.STOPPED and fake.closed
    if cancel:
        assert not any(action == "respond" for action, _ in fake.actions)


@pytest.mark.asyncio
async def test_rejected_provider_configuration_never_generates_greeting_or_listens(settings):
    from backend.app.testing.voice import FakeVoiceLiveProvider
    from backend.app.voice.service import VoiceManager, VoiceRuntime

    fake = FakeVoiceLiveProvider()

    async def rejected():
        await fake.emit("error")

    fake.create_session = rejected
    manager = VoiceManager(
        settings.model_copy(update={"test_mode": False}),
        Harness(),
        StubProvider(),
        provider_factory=lambda: fake,
    )
    sid = manager.repository.create(ACTOR).session_id
    socket = AsyncMock()
    runtime = VoiceRuntime(manager, socket, sid, ACTOR)
    runtime.graph.greeting = AsyncMock(side_effect=AssertionError("No greeting after rejection"))
    await runtime.run()
    runtime.graph.greeting.assert_not_called()
    assert runtime.state().status == VoiceStatus.FAILED and fake.closed
    assert "VOICE_CONFIGURATION_REJECTED" in str(socket.send_json.call_args_list)


@pytest.mark.asyncio
async def test_bare_yes_to_clarification_cannot_confirm_resolution(settings):
    from backend.app.voice.models import TroubleshootingAction

    state = VoiceConversation(
        session_id="f" * 32,
        owner_id="owner",
        tenant_id="local-demo",
        issue_summary="VPN broken",
        actions=[
            TroubleshootingAction(
                id="a", instruction="Check VPN", evidence_ids=[], result="unconfirmed"
            )
        ],
        turns=[TranscriptTurn(id="q", role="assistant", text="Is this affecting multiple users?")],
    )
    state, _ = await VoiceSupportGraph(settings, Harness(), StubProvider()).run(state, "yes", "t")
    assert (
        state.resolution_status != "user_confirmed_resolved"
        and state.actions[0].result == "unconfirmed"
    )


def test_explicit_resave_updates_final_outcome_without_duplicating_case(settings):
    from backend.app.agent.memory import CaseMemoryStore

    repo = SQLiteVoiceConversationRepository(settings.case_db_path)
    state = repo.create(ACTOR)

    def confirm(c):
        c.confirmed_profile = {
            "name": "Alex",
            "email": "a@example.com",
            "callback_number": "+12025550123",
        }
        c.issue_summary = "VPN broken"

    repo.update(state.session_id, ACTOR, confirm)
    store = CaseMemoryStore(settings.case_db_path)
    first = repo.create_case(state.session_id, ACTOR, store)

    def resolved(c):
        c.resolution_status = "user_confirmed_resolved"
        c.final_summary = "VPN — User confirmed success"
        c.status = VoiceStatus.COMPLETED

    repo.update(state.session_id, ACTOR, resolved)
    assert repo.create_case(state.session_id, ACTOR, store) == first
    case = store.load_case(first)
    assert case["voice_intake"]["resolution_status"] == "user_confirmed_resolved"
    assert case["ticket"]["summary"] == "VPN — User confirmed success"
    assert len(store.list_cases()) == 1
