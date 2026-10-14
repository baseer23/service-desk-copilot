import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from backend.app.voice import azure_voicelive
from backend.app.voice.azure_voicelive import AzureVoiceLiveProvider
from backend.app.voice.service import VoiceRuntime


@pytest.mark.asyncio
async def test_managed_speech_heartbeat_is_shared_and_does_not_block_audio_event_loop(
    settings, monkeypatch
):
    checks = []

    def require():
        checks.append(1)
        time.sleep(0.08)

    monkeypatch.setattr(
        azure_voicelive, "AzureUsageGuard", lambda config: SimpleNamespace(require_enabled=require)
    )
    provider = AzureVoiceLiveProvider(settings.model_copy(update={"data_platform": "azure"}))
    provider.started = time.monotonic()
    append = AsyncMock()
    provider.connection = SimpleNamespace(input_audio_buffer=SimpleNamespace(append=append))
    work = asyncio.gather(provider.send_audio("AAA="), provider.send_audio("AAA="))
    await asyncio.sleep(0.01)
    assert not work.done() and checks == [1]
    await work
    assert append.await_count == 2 and checks == [1]
    await provider.send_audio("AAA=")
    assert checks == [1]


@pytest.mark.asyncio
async def test_cancellation_before_managed_policy_check_finishes_prevents_sdk_response(
    settings, monkeypatch
):
    def require():
        time.sleep(0.08)

    monkeypatch.setattr(
        azure_voicelive, "AzureUsageGuard", lambda config: SimpleNamespace(require_enabled=require)
    )
    provider = AzureVoiceLiveProvider(settings.model_copy(update={"data_platform": "azure"}))
    provider.started = time.monotonic()
    create, cancel = AsyncMock(), AsyncMock()
    provider.connection = SimpleNamespace(response=SimpleNamespace(create=create, cancel=cancel))
    work = asyncio.create_task(provider.respond("Approved fictional guidance"))
    await asyncio.sleep(0.01)
    await provider.interrupt()
    await work
    create.assert_not_awaited()
    cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_managed_voice_heartbeat_and_state_writes_are_serialized(settings):
    active = 0
    calls = []
    def write(kind):
        nonlocal active
        active += 1
        assert active == 1, "Concurrent writes would race the Cosmos ETag"
        calls.append(kind)
        time.sleep(0.04)
        active -= 1
    runtime = object.__new__(VoiceRuntime)
    runtime.manager = SimpleNamespace(
        settings=settings.model_copy(update={"data_platform": "azure"}),
        repository=SimpleNamespace(
            update=lambda *args: write("state"), renew=lambda *args: write("lease"),
        ),
    )
    runtime.repository_lock = asyncio.Lock()
    runtime.stop = asyncio.Event()
    runtime.session_id, runtime.actor, runtime.lease = "fictional", None, "owned"
    await asyncio.gather(runtime.update(lambda state: None), runtime.renew_lease())
    assert sorted(calls) == ["lease", "state"]
    runtime.stop.set()
    await runtime.renew_lease()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_barge_in_during_final_greeting_read_suppresses_the_stale_greeting(settings):
    runtime = object.__new__(VoiceRuntime)
    runtime.manager = SimpleNamespace(settings=settings.model_copy(update={"test_mode": False}))
    runtime.epoch = 0
    runtime.stop = asyncio.Event()
    runtime.graph = SimpleNamespace(greeting=AsyncMock(return_value="Generated greeting"))
    runtime.respond = AsyncMock()
    reads = 0

    async def state():
        nonlocal reads
        reads += 1
        if reads == 2:
            runtime.epoch += 1  # speech arrives while Cosmos returns the final state
        return SimpleNamespace(turns=[])

    runtime.state_async = state
    await runtime.welcome()
    runtime.respond.assert_not_awaited()


@pytest.mark.asyncio
async def test_barge_in_clears_playback_and_cancels_before_managed_state_write():
    runtime = object.__new__(VoiceRuntime)
    runtime.epoch = 0
    runtime.response_id, runtime.assistant_id = "old-response", "old-assistant"
    runtime.pending_response = True
    runtime.cancelled = set()
    calls = []

    async def send(*args): calls.append("clear")
    async def interrupt(): calls.append("cancel")
    async def update(*args):
        assert calls == ["clear", "cancel"]
        calls.append("write")

    runtime.send, runtime.update = send, update
    runtime.provider = SimpleNamespace(interrupt=interrupt)
    await runtime.interrupt()
    assert calls == ["clear", "cancel", "write"]
    assert runtime.cancelled == {"old-response"}


@pytest.mark.asyncio
async def test_http_stop_waits_for_managed_turn_write_and_is_idempotent(settings):
    from backend.app.testing.voice import FakeVoiceLiveProvider
    from backend.app.tests.test_browser_voice import ACTOR, Harness, StubProvider
    from backend.app.voice.models import VoiceStatus
    from backend.app.voice.service import VoiceManager

    manager = VoiceManager(settings, Harness(), StubProvider(), provider_factory=FakeVoiceLiveProvider)
    state = manager.repository.create(ACTOR)
    runtime = VoiceRuntime(manager, AsyncMock(), state.session_id, ACTOR)
    manager.repository.claim(state.session_id, ACTOR, runtime.lease)
    manager.active[state.session_id] = runtime
    entered, release = asyncio.Event(), asyncio.Event()

    async def update():
        async with runtime.repository_lock:
            entered.set()
            await release.wait()
            manager.repository.update(state.session_id, ACTOR, lambda c: setattr(c, "issue_summary", "Kept"), runtime.lease)

    task = asyncio.create_task(update())
    await entered.wait()
    stopping = asyncio.create_task(manager.end_session(state.session_id, ACTOR))
    await asyncio.sleep(0)
    assert not stopping.done()
    release.set()
    await task
    stopped = await stopping
    assert stopped.status == VoiceStatus.STOPPED and stopped.issue_summary == "Kept"
    again = await manager.end_session(state.session_id, ACTOR)
    assert again.version == stopped.version


@pytest.mark.asyncio
async def test_managed_session_inspection_does_not_pause_audio_processing(settings):
    from backend.app.voice.service import VoiceManager

    manager = object.__new__(VoiceManager)
    manager.settings = settings.model_copy(update={"data_platform": "azure"})

    def inspect():
        time.sleep(0.08)
        return "Owned session"

    operation = asyncio.create_task(manager.store(inspect))
    await asyncio.sleep(0.01)
    assert not operation.done()
    assert await operation == "Owned session"
