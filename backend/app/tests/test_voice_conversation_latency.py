"""Offline regressions for conversational ordering and bounded paid work.

These exercise application behavior, not Azure speech quality or real latency.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from backend.app.agent.models import ToolRequest
from backend.app.models.provider_azure_openai import structured_request
from backend.app.testing.provider_stub import StubProvider
from backend.app.testing.voice import FakeVoiceLiveProvider
from backend.app.tests.test_browser_voice import ACTOR, Harness
from backend.app.voice.graph import VoiceSupportGraph, VoiceTurnSuperseded
from backend.app.voice.models import (
    ContactCandidate,
    TranscriptTurn,
    VoiceConversation,
    VoiceExtraction,
    VoiceReply,
    VoiceStatus,
    VoiceTurnProposal,
)
from backend.app.voice.service import VoiceManager, VoiceRuntime


def conversation():
    return VoiceConversation(
        session_id="a" * 32, tenant_id=ACTOR.tenant_id, owner_id=ACTOR.owner_id
    )


class Reasoning:
    def __init__(self, proposal, reply=None):
        self.proposal, self.reply = proposal, reply
        self.calls = []

    async def generate_structured(self, stage, context, schema):
        self.calls.append((stage, context))
        return self.proposal if stage == "voice_turn" else self.reply


@pytest.mark.asyncio
async def test_consecutive_clarifications_use_one_call_and_keep_context(settings):
    model = Reasoning(
        VoiceTurnProposal(
            extraction=VoiceExtraction(issue_summary="VPN connection failed"),
            clarification_question="What error does the VPN show?",
        )
    )
    graph = VoiceSupportGraph(
        settings.model_copy(update={"knowledge_scope": "broad"}), Harness(), model
    )
    state, reply = await graph.run(conversation(), "My VPN failed", "u1")
    state.turns.extend(
        [
            TranscriptTurn(id="u1", role="user", text="My VPN failed"),
            TranscriptTurn(id="a1", role="assistant", text=reply),
            TranscriptTurn(id="u2", role="user", text="Authentication failed"),
        ]
    )
    model.proposal = VoiceTurnProposal(
        extraction=VoiceExtraction(issue_details={"error": "Authentication failed"}),
        clarification_question="When did it last work?",
    )
    state, reply = await graph.run(state, "Authentication failed", "u2")
    assert [stage for stage, _ in model.calls] == ["voice_turn", "voice_turn"]
    assert state.issue_summary == "VPN connection failed"
    assert state.issue_details["error"] == "Authentication failed"
    assert model.calls[-1][1]["recent_turns"][-2]["text"] == "What error does the VPN show?"
    assert reply == "When did it last work?" and state.resolution_status == "unconfirmed"


@pytest.mark.asyncio
async def test_combined_plan_still_uses_mcp_and_verifies_evidence(settings):
    harness = Harness(
        items=[{"source": "Fictional VPN guide", "text": "Check the connection indicator."}]
    )
    model = Reasoning(
        VoiceTurnProposal(
            extraction=VoiceExtraction(issue_summary="VPN failed"),
            tool_calls=[ToolRequest(name="search_knowledge", arguments={"question": "VPN failed"})],
        )
    )

    async def generate(stage, context, schema):
        model.calls.append((stage, context))
        if stage == "voice_turn":
            return model.proposal
        return VoiceReply(
            reply="Check the indicator",
            action="Check the VPN connection indicator",
            evidence_ids=[context["evidence"][0]["id"]],
        )

    model.generate_structured = generate
    state, reply = await VoiceSupportGraph(
        settings.model_copy(update={"knowledge_scope": "broad"}), harness, model
    ).run(conversation(), "VPN failed", "u1")
    assert [stage for stage, _ in model.calls] == ["voice_turn", "voice_reply"]
    assert harness.calls == ["search_knowledge"]
    assert state.actions[0].evidence_ids == [state.evidence[0].id]
    assert reply.endswith("Did that work?")


@pytest.mark.asyncio
async def test_fast_question_cannot_skip_contact_confirmation_or_write_validation(settings):
    model = Reasoning(
        VoiceTurnProposal(
            extraction=VoiceExtraction(
                profile={"name": ContactCandidate(value="Alex Test", raw_quote="Alex Test")}
            ),
            clarification_question="What is the error?",
        )
    )
    graph = VoiceSupportGraph(
        settings.model_copy(update={"knowledge_scope": "broad"}), Harness(), model
    )
    state, reply = await graph.run(conversation(), "My name is Alex Test", "u1")
    assert state.pending_confirmation.field == "name" and not state.confirmed_profile
    assert reply == "I heard your name as Alex Test. Is that correct?"
    model.proposal = VoiceTurnProposal(
        extraction=VoiceExtraction(),
        tool_calls=[ToolRequest(name="save_case")],
        clarification_question="What is the error?",
    )
    with pytest.raises(ValueError, match="Write denied"):
        await graph.run(conversation(), "VPN failed", "u2")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "Can you disable the firewall?",
        "Can you restart the VPN client?",
        "What happens if you reset the router?",
    ],
)
async def test_fast_question_cannot_prescribe_ungrounded_or_elevated_steps(settings, question):
    model = Reasoning(
        VoiceTurnProposal(extraction=VoiceExtraction(), clarification_question=question)
    )
    state, reply = await VoiceSupportGraph(
        settings.model_copy(update={"knowledge_scope": "broad"}), Harness(), model
    ).run(conversation(), "VPN failed", "u1")
    assert state.resolution_status == "escalation_required" and not state.actions
    assert "firewall" not in reply


@pytest.mark.asyncio
async def test_barge_in_abandons_remaining_paid_stages_and_read_tools(settings):
    entered, release = asyncio.Event(), asyncio.Event()
    current = True
    calls = []

    async def generate(stage, context, schema):
        calls.append(stage)
        entered.set()
        await release.wait()
        return VoiceTurnProposal(
            extraction=VoiceExtraction(), tool_calls=[ToolRequest(name="search_knowledge")]
        )

    harness = Harness()
    graph = VoiceSupportGraph(
        settings.model_copy(update={"knowledge_scope": "broad"}),
        harness,
        SimpleNamespace(generate_structured=generate),
        is_current=lambda: current,
    )
    work = asyncio.create_task(graph.run(conversation(), "VPN failed", "old"))
    await entered.wait()
    current = False
    release.set()
    with pytest.raises(VoiceTurnSuperseded):
        await work
    assert calls == ["voice_turn"] and not harness.calls


def test_voice_prompt_is_not_duplicated_in_the_user_context(settings):
    from backend.app.voice.graph import PROMPT

    messages, _ = structured_request(
        settings,
        "voice_turn",
        {
            "instructions": PROMPT,
            "current_turn": "Fictional VPN issue",
            "confirmed_profile": {"name": "Alex Test"},
        },
        VoiceTurnProposal,
    )
    assert PROMPT in messages[0]["content"] and PROMPT not in messages[1]["content"]
    assert "Fictional VPN issue" in messages[1]["content"] and "Alex Test" in messages[1]["content"]


def runtime(settings):
    fake = FakeVoiceLiveProvider()
    manager = VoiceManager(settings, Harness(), StubProvider(), provider_factory=lambda: fake)
    state = manager.repository.create(ACTOR)
    instance = VoiceRuntime(manager, AsyncMock(), state.session_id, ACTOR)
    manager.repository.claim(state.session_id, ACTOR, instance.lease)
    manager.active[state.session_id] = instance
    return instance, fake


@pytest.mark.asyncio
async def test_vad_and_transcript_deltas_flow_without_transient_store_writes(settings):
    instance, fake = runtime(settings)
    instance.update = AsyncMock(
        side_effect=AssertionError("Transient events must not wait for Cosmos")
    )
    receiving = asyncio.create_task(instance.receiver())
    try:
        await fake.emit("speech.started", item_id="u1")
        await fake.emit("speech.stopped", item_id="u1")
        await fake.emit("user.delta", "VPN ", item_id="u1")
        await fake.emit("user.delta", "failed", item_id="u1")
        events = [await asyncio.wait_for(instance.outgoing.get(), 1) for _ in range(6)]
        assert [
            payload["text"] for kind, payload in events if kind == "transcript.user.partial"
        ] == ["VPN ", "failed"]
        instance.update.assert_not_awaited()
    finally:
        receiving.cancel()
        await asyncio.gather(receiving, return_exceptions=True)
        await fake.close_session()


@pytest.mark.asyncio
async def test_final_transcript_is_visible_while_durable_write_is_pending(settings):
    instance, fake = runtime(settings)
    entered, release = asyncio.Event(), asyncio.Event()
    update = instance.update

    async def slow_update(*args):
        entered.set()
        await release.wait()
        return await update(*args)

    instance.update = slow_update
    work = asyncio.create_task(instance.user_turn("u1", "Fictional VPN failed"))
    await entered.wait()
    kind, payload = instance.outgoing.get_nowait()
    assert kind == "transcript.user.final" and payload["text"] == "Fictional VPN failed"
    assert instance.turns.empty()  # Investigation still waits for the committed turn.
    release.set()
    await work
    assert instance.state().turns[0].id == "u1"
    await fake.close_session()


@pytest.mark.asyncio
async def test_cancel_drops_queued_audio_and_preserves_final_previous_turn(settings):
    instance, fake = runtime(settings)
    instance.assistant_id = "completed"
    await instance.update(
        lambda c: c.turns.append(TranscriptTurn(id="completed", role="assistant", text="Finished"))
    )
    await instance.send("audio", audio="old-output")
    await instance.send("transcript.user.partial", turn_id="u1", text="New")
    await instance.interrupt()
    queued = [instance.outgoing.get_nowait() for _ in range(instance.outgoing.qsize())]
    assert queued[0][0] == "playback.clear" and not any(kind == "audio" for kind, _ in queued)
    assert queued[-1][0] == "transcript.user.partial"
    assert not instance.interrupted_assistant_ids
    await instance.user_turn("u1", "New issue")
    assert instance.state().turns[0].state == "final"
    await fake.close_session()


@pytest.mark.asyncio
async def test_stale_queued_turn_cannot_start_reasoning(settings):
    instance, fake = runtime(settings)
    instance.epoch = 2
    instance.graph.run = AsyncMock()
    await instance.turns.put(("old", "Old utterance", 1))
    work = asyncio.create_task(instance.turn_worker())
    await asyncio.wait_for(instance.turns.join(), 1)
    instance.graph.run.assert_not_awaited()
    work.cancel()
    await asyncio.gather(work, return_exceptions=True)
    await fake.close_session()


@pytest.mark.asyncio
async def test_managed_cached_reads_preserve_isolation_and_refresh_after_writes(settings):
    instance, fake = runtime(settings)
    instance.manager.settings = settings.model_copy(update={"data_platform": "azure"})
    original = instance.manager.repository.get
    instance.manager.repository.get = lambda *args: (_ for _ in ()).throw(
        AssertionError("Unnecessary network read")
    )
    copy = await instance.state_async()
    copy.issue_summary = "Must not mutate committed cache"
    assert (await instance.state_async()).issue_summary == ""
    instance.manager.repository.get = original
    await instance.update(lambda c: setattr(c, "issue_summary", "Committed"))
    assert (
        (await instance.state_async()).issue_summary
        == original(instance.session_id, ACTOR).issue_summary
        == "Committed"
    )
    await instance.manager.end_session(instance.session_id, ACTOR)
    assert (await instance.state_async()).status == VoiceStatus.STOPPED
    await fake.close_session()


@pytest.mark.asyncio
async def test_cancelled_before_dispatch_does_not_discard_next_response(settings):
    instance, fake = runtime(settings)
    respond = fake.respond
    attempts = 0

    async def render(text):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await instance.interrupt()
            return False
        await respond(text)
        return True

    fake.respond = render
    await instance.respond("Old reply?")
    assert not instance.created_epochs
    receiving = asyncio.create_task(instance.receiver())
    try:
        await instance.respond("New reply?")
        for _ in range(30):
            kind, payload = await asyncio.wait_for(instance.outgoing.get(), 1)
            if kind == "session.snapshot":
                break
        else:
            raise AssertionError("New response was discarded")
        assert not instance.created_epochs
        assert instance.state().turns[-1].text == "New reply?"
        assert instance.state().turns[-1].state == "final"
        assert instance.state().turns[0].state == "interrupted"
    finally:
        receiving.cancel()
        await asyncio.gather(receiving, return_exceptions=True)
        await fake.close_session()


@pytest.mark.asyncio
async def test_late_transcription_is_correlated_to_its_own_speech_epoch(settings):
    instance, fake = runtime(settings)
    instance.graph.run = AsyncMock()
    receiving = asyncio.create_task(instance.receiver())
    try:
        await fake.emit("speech.started", item_id="old")
        await fake.emit("speech.started", item_id="current")
        await fake.emit("user.final", "Older utterance", item_id="old")
        await fake.emit("user.final", "Current utterance", item_id="current")
        old = await asyncio.wait_for(instance.turns.get(), 1)
        current = await asyncio.wait_for(instance.turns.get(), 1)
        assert old == ("old", "Older utterance", 1)
        assert current == ("current", "Current utterance", 2)
        assert [turn.text for turn in instance.state().turns] == [
            "Older utterance",
            "Current utterance",
        ]
    finally:
        receiving.cancel()
        await asyncio.gather(receiving, return_exceptions=True)
        await fake.close_session()


@pytest.mark.asyncio
async def test_last_permitted_reply_is_finalized_before_the_session_ends(settings):
    instance, fake = runtime(settings)
    instance.last_turn = True
    instance.approved = "Verified last question?"
    instance.assistant_id, instance.response_id = "last", "r1"
    instance.final_received = True
    await instance.update(
        lambda c: c.turns.append(
            TranscriptTurn(
                id="last",
                role="assistant",
                text=instance.approved,
                state="partial",
            )
        )
    )
    receiving = asyncio.create_task(instance.receiver())
    await fake.emit("response.done", response_id="r1", status="completed")
    await asyncio.wait_for(receiving, 1)
    assert instance.stop.is_set() and instance.state().turns[-1].state == "final"
    await fake.close_session()


def test_unfunded_voice_start_is_rejected_before_creating_an_unusable_session(settings):
    from backend.app.core.azure_usage import AzureUsageGuard
    from backend.app.tests.test_azure_readiness import azure_settings
    from backend.app.voice.auth import VoiceError

    config = azure_settings(settings, azure_max_voice_session_minutes=10)
    guard = AzureUsageGuard(config)
    reservation = guard.reserve("session_voice")
    guard.finish(reservation, seconds=1)
    manager = VoiceManager(config, Harness(), StubProvider())
    manager.repository.create = Mock(wraps=manager.repository.create)
    with pytest.raises(VoiceError) as error:
        manager.create(ACTOR)
    assert error.value.code == "AZURE_VOICE_NOT_READY" and error.value.status_code == 503
    manager.repository.create.assert_not_called()
    assert manager.create(ACTOR, mode="text").mode == "text"
    manager.repository.create.assert_called_once()
