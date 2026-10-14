"""Bounded browser/provider duplex and durable, owner-scoped session lifecycle."""

import asyncio
import base64
import logging
import re
import time
import uuid
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from anyio import CancelScope
from fastapi import WebSocket, WebSocketDisconnect

from backend.app.core.azure_usage import AzureUsageGuard

from .auth import Actor, VoiceError
from .graph import GREETING, VoiceSupportGraph
from .models import (
    TERMINAL,
    ClientEnvelope,
    ServerEnvelope,
    TranscriptTurn,
    VoiceConversation,
    VoiceStatus,
)
from .repository import SQLiteVoiceConversationRepository


class VoiceRuntime:
    def __init__(self, manager: Any, socket: WebSocket, session_id: str, actor: Actor,
                 conversation: VoiceConversation | None = None):
        self.manager, self.socket, self.session_id, self.actor = manager, socket, session_id, actor
        self.lease = uuid.uuid4().hex
        self.mode = (conversation or manager.repository.get(session_id, actor)).mode
        self.loop = asyncio.get_running_loop()
        self.provider = (
            manager.provider_factory() if manager.provider_factory else manager._provider(self.mode)
        )
        self.outgoing: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=64)
        self.audio: asyncio.Queue[str] = asyncio.Queue(maxsize=128)
        self.turns: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=4)
        self.sequence = 0
        self.client_sequence = 0
        self.started = time.monotonic()
        self.stop = asyncio.Event()
        self.reason = "client_disconnected"
        self.seen: set[str] = set()
        self.seen_order: deque[str] = deque()
        self.response_id = ""
        self.cancelled: set[str] = set()
        self.assistant_id = ""
        self.approved = ""
        self.rendered = ""
        self.render_channel = ""
        self.final_received = False
        self.text_usage_token = ""
        self.last_turn = False
        self.pending_response = False
        self.created_epochs: deque[int] = deque()
        self.epoch = 0
        self.repository_lock = asyncio.Lock()
        self.delivered_frames = 0
        self.last_delivery_notice = 0.0
        self.graph = VoiceSupportGraph(
            manager.settings, manager.harness, manager.reasoning, self.activity
        )

    def state(self) -> VoiceConversation:
        return self.manager.repository.get(self.session_id, self.actor)

    async def state_async(self) -> VoiceConversation:
        return await self.store(self.manager.repository.get, self.session_id, self.actor)

    async def store(self, function, *arguments, **keywords):
        if self.manager.settings.data_platform == "azure":
            return await asyncio.to_thread(function, *arguments, **keywords)
        return function(*arguments, **keywords)

    async def update(
        self, fn: Callable[[VoiceConversation], None], event_id: str | None = None
    ) -> VoiceConversation:
        async with self.repository_lock:
            if self.stop.is_set():
                raise VoiceError("Voice session stopped")
            return await self.store(
                self.manager.repository.update, self.session_id, self.actor,
                fn, self.lease, event_id,
            )

    async def renew_lease(self) -> None:
        async with self.repository_lock:
            if not self.stop.is_set():
                await self.store(self.manager.repository.renew, self.session_id, self.lease)

    async def send(self, kind: str, **payload: Any) -> None:
        await asyncio.wait_for(self.outgoing.put((kind, payload)), 2)

    async def activity(self, label: str) -> None:
        status = (
            VoiceStatus.USING_TOOLS
            if label in {"Searching knowledge", "Checking previous incidents"}
            else VoiceStatus.PROCESSING
        )
        await self.update(lambda c: setattr(c, "status", status), uuid.uuid4().hex)
        await self.send("status", status=status.value)
        await self.send("activity", label=label)

    async def sender(self) -> None:
        while True:
            kind, payload = await self.outgoing.get()
            self.sequence += 1
            envelope = ServerEnvelope(
                type=kind, session_id=self.session_id, sequence=self.sequence, payload=payload
            )
            await asyncio.wait_for(self.socket.send_json(envelope.model_dump()), 5)
            self.outgoing.task_done()

    async def publish_state(self) -> None:
        await self.send("session.snapshot", conversation=(await self.state_async()).public())

    async def respond(self, text: str) -> None:
        epoch = self.epoch
        self.approved = text
        self.rendered = ""
        self.render_channel = ""
        self.final_received = False
        self.assistant_id = uuid.uuid4().hex
        assistant_id = self.assistant_id

        def apply(c: VoiceConversation) -> None:
            c.turns.append(
                TranscriptTurn(id=assistant_id, role="assistant", text=text, state="partial")
            )
            if c.status not in TERMINAL:
                c.status = VoiceStatus.ASSISTANT_SPEAKING

        await self.update(apply)
        if self.stop.is_set() or epoch != self.epoch:
            return
        await self.send("transcript.assistant.partial", turn_id=self.assistant_id, text="")
        if self.stop.is_set() or epoch != self.epoch:
            return
        self.pending_response = True
        self.created_epochs.append(epoch)
        await self.provider.respond(text)
        await self.send("status", status=(await self.state_async()).status.value)

    async def interrupt(self) -> None:
        self.epoch += 1
        if self.response_id:
            self.cancelled.add(self.response_id)
        # Stop audible output before waiting for durable managed-state writes.
        await self.send("playback.clear")
        if self.pending_response:
            await self.provider.interrupt()
            self.pending_response = False
        if self.assistant_id:
            assistant_id = self.assistant_id

            def apply(c: VoiceConversation) -> None:
                for t in c.turns:
                    if t.id == assistant_id:
                        t.state = "interrupted"

            await self.update(apply)
        self.response_id = ""

    async def user_turn(self, turn_id: str, text: str) -> None:
        text = text.strip()
        if not text or len(text) > 8000:
            raise VoiceError("Text turn must contain 1 to 8000 characters", 422)
        if any(t.id == turn_id for t in (await self.state_async()).turns):
            return
        if not self.manager.settings.test_mode:
            count = await self.store(self.manager.usage_guard.turn,
                self.text_usage_token or self.provider.usage_token
            )
            self.last_turn = count >= self.manager.settings.azure_max_turns_per_session
        if len((await self.state_async()).turns) >= self.manager.settings.voice_max_turns:
            self.reason = "turn_limit"
            self.stop.set()
            return

        def apply(c: VoiceConversation) -> None:
            if c.status in TERMINAL:
                raise VoiceError("Voice session ended")
            c.turns.append(TranscriptTurn(id=turn_id, role="user", text=text))
            c.status = VoiceStatus.PROCESSING

        await self.update(apply, "user:" + turn_id)
        await self.send("transcript.user.final", turn_id=turn_id, text=text)
        await asyncio.wait_for(self.turns.put((turn_id, text)), 2)

    async def reader(self) -> None:
        while True:
            raw = await self.socket.receive_text()
            if len(raw.encode()) > 65536:
                raise VoiceError("Voice message exceeds the size limit", 413)
            try:
                message = ClientEnvelope.model_validate_json(raw)
            except Exception:  # noqa: BLE001 - sanitize external failures at the boundary
                raise VoiceError("Malformed voice message", 422) from None
            if message.session_id != self.session_id:
                raise VoiceError("Voice message session mismatch", 403)
            if message.sequence <= self.client_sequence:
                continue
            if message.sequence != self.client_sequence + 1:
                raise VoiceError("Voice message arrived out of order", 422)
            self.client_sequence = message.sequence
            if self.last_turn and message.type in {"audio.append", "audio.commit", "text.input"}:
                continue  # Finish the last permitted reply without dispatching further input.
            if message.type == "session.stop":
                self.reason = "explicit_stop"
                self.stop.set()
                return
            if message.type == "response.cancel":
                await self.interrupt()
            elif message.type == "audio.append":
                if self.mode == "text":
                    raise VoiceError(
                        "Text support does not accept microphone audio",
                        422,
                        "VOICE_TEXT_AUDIO_REJECTED",
                    )
                audio = message.payload.get("audio")
                try:
                    decoded = base64.b64decode(audio, validate=True)
                    if not 2 <= len(decoded) <= 9600 or len(decoded) % 2:
                        raise ValueError("Invalid PCM frame")
                except Exception:  # noqa: BLE001 - sanitize external failures at the boundary
                    raise VoiceError("Invalid PCM16 audio frame", 422) from None
                # Bound memory while absorbing real managed-policy/network
                # latency: at most 2.56 seconds of normal 20ms PCM frames.
                try:
                    self.audio.put_nowait(audio)
                except asyncio.QueueFull:
                    raise VoiceError(
                        "The microphone connection is too slow. Reconnect or use text support.",
                        503, "AUDIO_QUEUE_FULL",
                    ) from None
            elif message.type == "audio.commit":
                # Wait for the queued frames before committing the same turn.
                await asyncio.wait_for(self.audio.join(), 2)
                await self.provider.commit_audio()
            elif message.type == "text.input":
                text = message.payload.get("text")
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= 8000:
                    raise VoiceError("Invalid text turn", 422)
                await self.interrupt()
                await self.provider.send_text(text)
                await self.user_turn("text:" + str(message.sequence) + ":" + self.lease, text)
            elif message.type == "ping":
                await self.send("pong")

    async def audio_sender(self) -> None:
        while True:
            audio = await self.audio.get()
            try:
                if not self.last_turn:
                    await asyncio.wait_for(self.provider.send_audio(audio), 5)
                    self.delivered_frames += 1
                    now = time.monotonic()
                    if self.delivered_frames == 1 or now - self.last_delivery_notice >= 1:
                        # SDK append completed; Azure's VAD/transcription events
                        # separately establish recognition. No audio is persisted.
                        await self.send("audio.delivery", frames=self.delivered_frames)
                        self.last_delivery_notice = now
            finally:
                self.audio.task_done()

    async def receiver(self) -> None:
        async for event in self.provider.events():
            if event.id and event.id in self.seen:
                continue
            if event.id:
                self.seen.add(event.id)
                self.seen_order.append(event.id)
                if len(self.seen_order) > 4096:
                    self.seen.discard(self.seen_order.popleft())
            response_id = event.data.get("response_id", "")
            if response_id in self.cancelled:
                continue
            if event.type == "speech.started":
                await self.interrupt()
                await self.update(
                    lambda c: setattr(c, "status", VoiceStatus.USER_SPEAKING), event.id
                )
                await self.send("status", status="user_speaking")
            elif event.type == "speech.stopped":
                await self.activity("Transcribing")
            elif event.type == "user.delta":
                await self.send(
                    "transcript.user.partial",
                    turn_id=event.data.get("item_id", event.id),
                    text=event.text,
                )
            elif event.type == "user.final":
                await self.user_turn(event.data.get("item_id") or event.id, event.text)
            elif event.type == "response.created":
                created_epoch = self.created_epochs.popleft() if self.created_epochs else -1
                if created_epoch != self.epoch:
                    self.cancelled.add(response_id)
                    continue
                self.response_id = response_id
            elif event.type in {"assistant.delta", "assistant.final"}:
                channel = event.data.get("channel", "audio_transcript")
                if not self.response_id or response_id != self.response_id:
                    continue
                if self.render_channel and self.render_channel != channel:
                    continue
                self.render_channel = channel
                value = (
                    self.rendered + event.text if event.type == "assistant.delta" else event.text
                )

                def normalize(text: str) -> str:
                    return re.sub(r"\s+", " ", text).strip().casefold()

                expected = normalize(self.approved)
                actual = normalize(value)
                if not expected.startswith(actual) or (
                    event.type == "assistant.final" and actual != expected
                ):
                    await self.interrupt()
                    raise VoiceError(
                        "Speech renderer diverged from the verified reply", 502,
                        "VOICE_RENDERER_MISMATCH",
                    )
                self.rendered = value
                if event.type == "assistant.delta":
                    await self.send(
                        "transcript.assistant.partial", turn_id=self.assistant_id, text=event.text
                    )
                else:
                    self.final_received = True

                    assistant_id = self.assistant_id

                    def finalized(c: VoiceConversation, turn_id: str = assistant_id) -> None:
                        for turn in c.turns:
                            if turn.id == turn_id:
                                turn.state = "final"

                    await self.update(finalized)
                    await self.send(
                        "transcript.assistant.final", turn_id=self.assistant_id, text=self.approved
                    )
            elif event.type == "audio":
                if not self.response_id or response_id != self.response_id:
                    continue
                audio = event.data.get("audio", "")
                if len(audio) > 65536:
                    raise VoiceError("Provider audio frame exceeds the limit", 502)
                await self.send("audio", audio=audio, response_id=response_id)
            elif event.type == "response.done":
                if not self.response_id or response_id != self.response_id:
                    continue
                if event.data.get("status") in {"failed", "incomplete"}:
                    raise VoiceError("Voice synthesis did not complete", 502)
                if not self.final_received:
                    raise VoiceError("Voice synthesis returned no verified transcript", 502)
                if self.last_turn:
                    self.reason = "turn_limit"
                    self.stop.set()
                    return
                if (await self.state_async()).status == VoiceStatus.COMPLETED:
                    self.reason = "completed"
                    await self.publish_state()
                    await asyncio.wait_for(self.outgoing.join(), 2)
                    self.stop.set()
                    return
                await self.update(
                    lambda c: setattr(
                        c,
                        "status",
                        (
                            VoiceStatus.AWAITING_CONFIRMATION
                            if c.pending_confirmation
                            else VoiceStatus.LISTENING
                        ),
                    ),
                    event.id,
                )
                self.response_id = ""
                self.pending_response = False
                await self.publish_state()
            elif event.type == "tool":
                # Native SDK function events are supported, but app graph owns dispatch.
                # No tools are advertised to the speech renderer; reject unexpected calls.
                await self.provider.tool_result(
                    event.data.get("call_id", ""),
                    {
                        "error": (
                            "Use the application investigation graph; direct tool dispatch is"
                            " prohibited"
                        )
                    },
                )
            elif event.type == "error":
                raise VoiceError("Voice provider failed", 502)
            # Only a verified renderer transcript reaches the stable assistant bubble.
        raise VoiceError("Voice provider disconnected", 502)

    async def turn_worker(self) -> None:
        while True:
            turn_id, text = await self.turns.get()
            epoch = self.epoch
            try:
                await self.activity("Preparing response")
                state, reply = await self.graph.run((await self.state_async()), text, turn_id)
                if self.stop.is_set() or epoch != self.epoch:
                    continue

                def apply(c: VoiceConversation, turn_state: VoiceConversation = state) -> None:
                    # Stop/auth lease checked atomically by repository; preserve turns
                    # arriving while this model turn was being processed.
                    for field in (
                        "candidates",
                        "confirmed_profile",
                        "profile_region",
                        "pending_confirmation",
                        "confirmation_retries",
                        "manual_contact_fields",
                        "issue_summary",
                        "issue_details",
                        "evidence",
                        "actions",
                        "resolution_status",
                        "final_summary",
                        "status",
                    ):
                        setattr(c, field, getattr(turn_state, field))

                await self.update(apply)
                await self.respond(reply)
            finally:
                self.turns.task_done()

    async def welcome(self) -> None:
        epoch = self.epoch
        if (await self.state_async()).turns:
            return
        greeting = GREETING if self.manager.settings.test_mode else await self.graph.greeting()
        # Speech and typed input may arrive during generation. They take priority.
        latest = await self.state_async()
        if not self.stop.is_set() and epoch == self.epoch and not latest.turns:
            await self.respond(greeting)

    async def watchdog(self) -> None:
        while True:
            await asyncio.sleep(5)
            state = (await self.state_async())
            if state.status in TERMINAL and state.status != VoiceStatus.COMPLETED:
                self.reason = state.failure_category or "explicit_stop"
                self.stop.set()
                return
            if time.monotonic() - self.started > self.manager.settings.voice_session_timeout_sec:
                self.reason = "session_timeout"
                self.stop.set()
                return
            if not self.manager.settings.test_mode:
                await self.store(self.manager.usage_guard.require_enabled)
                if (
                    time.monotonic() - self.started
                    >= self.manager.settings.azure_max_voice_session_minutes * 60
                ):
                    self.reason = "session_timeout"
                    self.stop.set()
                    return
            await self.renew_lease()

    async def run(self) -> None:
        await self.store(self.manager.repository.claim, self.session_id, self.actor, self.lease)
        self.manager.active[self.session_id] = self
        await self.socket.accept()
        tasks: list[asyncio.Task[Any]] = []
        try:
            if not self.manager.settings.test_mode and self.mode == "text":
                self.text_usage_token = await self.store(
                    self.manager.usage_guard.reserve, "session_text"
                )

            async def prepare() -> None:
                await asyncio.wait_for(self.provider.create_session(), 25)

                async def configured() -> None:
                    async for event in self.provider.events():
                        if event.type == "error":
                            raise VoiceError(
                                "The provider rejected the session configuration",
                                502,
                                "VOICE_CONFIGURATION_REJECTED",
                            )
                        if event.type == "configured":
                            return
                    raise VoiceError("The provider disconnected during preparation", 502)

                await asyncio.wait_for(configured(), 10)

            # Configuration, rather than greeting generation, gates capture.
            # Keep the lease renewed and honor Stop during provider preparation.
            opening = asyncio.create_task(prepare())
            waiter = asyncio.create_task(self.stop.wait())
            watchdog = asyncio.create_task(self.watchdog())
            tasks = [opening, waiter, watchdog]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            if opening not in done or self.stop.is_set():
                return
            await self.update(lambda c: setattr(c, "status", VoiceStatus.LISTENING))
            tasks += [
                asyncio.create_task(fn())
                for fn in (
                    self.sender,
                    self.reader,
                    self.audio_sender,
                    self.receiver,
                    self.turn_worker,
                )
            ]
            await self.publish_state()
            welcome = asyncio.create_task(self.welcome())
            tasks.append(welcome)
            running = [task for task in tasks if task is not opening]
            while True:
                done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    if task is not waiter:
                        task.result()
                if done == {welcome}:
                    running.remove(welcome)
                    continue
                break
        except WebSocketDisconnect:
            self.reason = "client_disconnected"
        except asyncio.CancelledError:
            self.reason = "client_disconnected"
        except Exception as exc:  # noqa: BLE001 - sanitize external failures at the boundary
            self.reason = "connection_failed"
            error_code = exc.code if isinstance(exc, VoiceError) else "VOICE_CONNECTION_FAILED"
            logging.getLogger(__name__).warning(
                "Voice runtime failed: %s code=%s provider_code=%s",
                type(exc).__name__, error_code, getattr(self.provider, "last_error_code", ""),
            )
            try:
                await self.socket.send_json(
                    ServerEnvelope(
                        type="error",
                        session_id=self.session_id,
                        sequence=self.sequence + 1,
                        payload={
                            "message": "Voice connection ended. Reconnect or use text support.",
                            "code": error_code,
                        },
                    ).model_dump()
                )
            except Exception:  # noqa: BLE001, S110
                pass  # Close is best-effort; no raw SDK errors in logs.
        finally:
            with CancelScope(shield=True):
                self.stop.set()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await self.provider.close_session()
                if self.text_usage_token:
                    await self.store(self.manager.usage_guard.finish,
                        self.text_usage_token, seconds=time.monotonic() - self.started
                    )

                def end(c: VoiceConversation) -> None:
                    if c.status != VoiceStatus.COMPLETED:
                        c.status = (
                            VoiceStatus.STOPPED
                            if self.reason
                            in {"explicit_stop", "client_disconnected", "server_shutdown"}
                            else VoiceStatus.FAILED
                        )
                        c.failure_category = self.reason

                async with self.repository_lock:
                    try:
                        await self.store(self.manager.repository.update,
                            self.session_id, self.actor, end, self.lease, "end:" + self.lease
                        )
                    except VoiceError:
                        pass  # Close is best-effort; no raw SDK errors in logs.
                    await self.store(
                        self.manager.repository.release, self.session_id, self.actor, self.lease
                    )
                try:
                    await self.socket.close()
                except Exception:  # noqa: BLE001, S110
                    pass  # Close is best-effort; no raw SDK errors in logs.


class VoiceManager:
    def __init__(
        self, settings: Any, harness: Any, reasoning: Any, *, provider_factory: Any = None
    ) -> None:
        self.settings, self.harness, self.reasoning = settings, harness, reasoning
        self.usage_guard = AzureUsageGuard(settings)
        if settings.data_platform == "azure":
            from backend.app.store.azure_records import CosmosRecords

            from .azure_repository import CosmosVoiceConversationRepository

            self.repository = CosmosVoiceConversationRepository(CosmosRecords(settings))
        else:
            self.repository = SQLiteVoiceConversationRepository(settings.case_db_path)
        self.provider_factory = provider_factory
        self.active: dict[str, VoiceRuntime] = {}

    async def store(self, function: Callable, *args: Any) -> Any:
        if self.settings.data_platform == "azure":
            return await asyncio.to_thread(function, *args)
        return function(*args)

    def _provider(self, mode: str = "voice") -> Any:
        if self.settings.test_mode and self.settings.voice_provider == "fake":
            from backend.app.testing.voice import FakeVoiceLiveProvider

            return FakeVoiceLiveProvider()
        if self.settings.test_mode:
            raise VoiceError("Live Azure voice is prohibited in TEST_MODE", 503)
        if mode == "text":
            from .providers import TextSupportTransport

            return TextSupportTransport()
        from .azure_voicelive import AzureVoiceLiveProvider

        return AzureVoiceLiveProvider(self.settings)

    def ready(self, mode: str = "voice") -> None:
        from backend.app.diagnostics.readiness import configuration_readiness

        if not self.settings.test_mode:
            self.usage_guard.require_enabled()
        report = configuration_readiness(self.settings)
        if not self.settings.test_mode and (
            not report.reasoning.configured or self.reasoning is None
        ):
            raise VoiceError("Azure reasoning is not configured", 503, "AZURE_REASONING_NOT_READY")
        if mode == "voice" and not report.voice.configured:
            raise VoiceError(
                "Voice Live is disabled or not configured", 503, "AZURE_VOICE_NOT_READY"
            )
        if not self.settings.test_mode and self.settings.voice_provider != "azure_voicelive":
            raise VoiceError("Only Azure Voice Live is supported", 503, "AZURE_PROVIDER_REQUIRED")

    def create(self, actor: Actor, mode: str = "voice") -> VoiceConversation:
        self.ready(mode)
        if self.settings.azure_live_test_mode and not self.settings.test_mode:
            self.usage_guard.claim_operator(actor.tenant_id + ":" + actor.owner_id)
        cutoff = (
            datetime.now(UTC) - timedelta(hours=self.settings.voice_transcript_retention_hours)
        ).isoformat()
        self.repository.purge(actor.tenant_id, cutoff)
        state = self.repository.create(actor)
        return self.repository.update(state.session_id, actor, lambda c: setattr(c, "mode", mode))

    async def connect(self, socket: WebSocket, session_id: str, actor: Actor) -> None:
        conversation = await self.store(self.repository.get, session_id, actor)
        await self.store(self.ready, conversation.mode)
        runtime = VoiceRuntime(self, socket, session_id, actor, conversation)
        try:
            await runtime.run()
        finally:
            if self.active.get(session_id) is runtime:
                self.active.pop(session_id, None)

    def end(self, session_id: str, actor: Actor) -> VoiceConversation:
        self.repository.get(session_id, actor)  # Confirm ownership before signalling transport.
        if session_id in self.active:
            self.active[session_id].reason = "explicit_stop"
            runtime = self.active[session_id]
            runtime.loop.call_soon_threadsafe(runtime.stop.set)
        def apply(c: VoiceConversation) -> None:
            if c.status != VoiceStatus.COMPLETED:
                c.status = VoiceStatus.STOPPED
                c.failure_category = "explicit_stop"

        return self.repository.update(session_id, actor, apply, event_id="explicit_stop")

    async def end_session(self, session_id: str, actor: Actor) -> VoiceConversation:
        state = await self.store(self.repository.get, session_id, actor)
        if state.status == VoiceStatus.COMPLETED or state.failure_category == "explicit_stop":
            return state
        runtime = self.active.get(session_id)
        if runtime:
            # The HTTP stop, heartbeat, turn updates and connection cleanup share
            # the same lock; a successful stop cannot race an in-flight ETag write.
            async with runtime.repository_lock:
                return await self.store(self.end, session_id, actor)
        return await self.store(self.end, session_id, actor)

    async def close(self) -> None:
        for runtime in list(self.active.values()):
            runtime.reason = "server_shutdown"
            runtime.stop.set()
