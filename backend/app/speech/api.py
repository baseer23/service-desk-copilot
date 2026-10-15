"""Authenticated continuous recognition gateway; recognized text is client-owned.

The completed transcript is submitted through the ordinary /ask LangGraph route.
There is no independent speech agent, audio model, TTS, or conversation database.
"""

import asyncio
import ipaddress
import json
import time
from contextlib import suppress
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket

from backend.app.core.authentication import SESSION_COOKIE, authenticate
from backend.app.core.principal import current_principal, principal_scope, verify_principal
from backend.app.speech.azure_stt import AzureSpeechRecognizer
from backend.app.speech.transcript import Transcript, standalone_stop
from backend.app.speech.usage import SpeechMeter


def browser_guard(request, settings):
    origin = request.headers.get("origin")
    parts = urlsplit(origin or "")
    same_origin = parts.netloc == request.headers.get("host") and parts.scheme == (
        "https" if request.url.scheme in {"https", "wss"} else "http"
    )
    if not origin or (origin not in settings.allowed_origins and not same_origin):
        raise HTTPException(403, "This connection is not permitted.")
    if settings.deployment_mode == "local":
        client = request.client.host if request.client else ""
        try:
            loopback = ipaddress.ip_address(client).is_loopback
        except ValueError:
            loopback = client == "testclient" and settings.test_mode
        hostname = urlsplit("http://" + request.headers.get("host", "")).hostname
        if not loopback or hostname not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            raise HTTPException(403, "Local microphone access requires loopback.")


def speech_readiness(settings):
    configured = bool(settings.speech_enabled and settings.azure_speech_endpoint)
    return {"configured": configured, "ready": configured, "recognition_only": True}


class SpeechService:
    def __init__(
        self, settings, recognizer_factory=AzureSpeechRecognizer, meter_factory=SpeechMeter
    ):
        self.settings = settings
        self.recognizer_factory = recognizer_factory
        self.meter_factory = meter_factory
        self.active = {}

    async def close(self):
        sessions = list(self.active.items())
        for stop, _details in sessions:
            stop.set()
        if sessions:
            await asyncio.wait_for(
                asyncio.gather(*(details[1].wait() for _, details in sessions)), 12
            )

    async def close_for(self, actor):
        for stop, (owner, _done) in list(self.active.items()):
            if (owner.tenant_id, owner.owner_id) == (actor.tenant_id, actor.owner_id):
                stop.set()

    async def stream(self, ws, actor):
        stop = asyncio.Event()
        finished = asyncio.Event()
        try:
            adapter = self.recognizer_factory(self.settings)
            meter = self.meter_factory(self.settings)
        except Exception:  # noqa: BLE001 - Configuration/storage failures stay private.
            await ws.send_json(
                {
                    "type": "error",
                    "message": "Speech recognition could not initialize. Try again later.",
                }
            )
            await ws.close(code=1011)
            return
        self.active[stop] = (actor, finished)
        transcript = Transcript()
        send_lock = asyncio.Lock()
        state = "connecting"
        started = time.monotonic()
        reason = "explicit_stop"

        async def send(kind, **payload):
            async with send_lock:
                await asyncio.wait_for(ws.send_json({"type": kind, **payload}), 3)

        async def input_loop():
            nonlocal reason
            frames, last_frame = 0, time.monotonic()
            while not stop.is_set():
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    reason = "client_disconnected"
                    stop.set()
                    return
                frame = message.get("bytes")
                if frame is not None:
                    if len(frame) != 640:
                        raise ValueError("Invalid PCM frame")
                    frames += 1
                    # Prevent clients from sending prerecorded hours faster than
                    # wall clock while reserving only connected recognition time.
                    if frames > (time.monotonic() - last_frame + 2) * 50:
                        raise ValueError("Speech input exceeded real-time pacing")
                    await asyncio.to_thread(adapter.write, frame)
                else:
                    raw = message.get("text", "")
                    if len(raw) > 1024:
                        raise ValueError("Invalid control frame")
                    control = json.loads(raw)
                    if control == {"type": "stop"}:
                        stop.set()
                        return
                    if control != {"type": "ping"}:
                        raise ValueError("Invalid control message")

        async def event_loop():
            nonlocal state, reason
            retries = 0
            last_generation = 0
            while not stop.is_set():
                event = await adapter.events.get()
                generation = event.get("generation", adapter.generation)
                if generation < adapter.generation:
                    continue
                kind = event["type"]
                if kind in {"partial", "final"}:
                    retries = 0
                    if standalone_stop(event["text"]):
                        if kind == "final":
                            reason = "spoken_stop"
                            stop.set()
                            return
                        continue
                    text = (
                        event["text"]
                        if kind == "partial"
                        else transcript.finalize(generation, event["id"], event["text"])
                    )
                    if text:
                        await send(
                            "transcript." + kind, text=text, id=f"{generation}:{event['id']}"
                        )
                elif kind == "interrupted":
                    if last_generation == generation:
                        continue
                    last_generation = generation
                    state = "reconnecting"
                    await send("state", state=state)
                    while not stop.is_set():
                        retries += 1
                        if retries > 5:
                            raise RuntimeError("Speech reconnect attempts exhausted")
                        await adapter.disconnect()
                        meter.disconnected()
                        try:
                            await asyncio.wait_for(stop.wait(), min(8, 0.5 * 2 ** (retries - 1)))
                            return
                        except TimeoutError:
                            pass
                        await asyncio.to_thread(meter.heartbeat)
                        meter.connected()
                        try:
                            await adapter.start(replay=True)
                        except Exception:  # noqa: BLE001,S112 - Bounded SDK recovery, no raw diagnostics.
                            continue
                        state = "listening"
                        await send("state", state=state)
                        break
                elif kind == "error":
                    raise RuntimeError("Speech recognition service rejected the session")

        async def heartbeat():
            while not stop.is_set():
                await asyncio.to_thread(meter.heartbeat)
                if actor.expires_at and actor.expires_at <= time.time():
                    raise PermissionError("Authentication expired")
                await send(
                    "usage", listening_seconds=round(time.monotonic() - started), **meter.snapshot()
                )
                try:
                    await asyncio.wait_for(stop.wait(), 1)
                except TimeoutError:
                    pass

        tasks = []
        try:
            await asyncio.to_thread(meter.start)
            meter.connected()  # includes handshake time conservatively
            await send("state", state=state)
            incoming = asyncio.create_task(input_loop())
            opening = asyncio.create_task(adapter.start())
            interrupted = asyncio.create_task(stop.wait())
            tasks = [incoming, opening, interrupted]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if incoming in done:
                incoming.result()
            if stop.is_set():
                return
            await opening
            state = "listening"
            await send("state", state=state)
            tasks = [incoming, interrupted] + [
                asyncio.create_task(fn()) for fn in (event_loop, heartbeat)
            ]
            done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except Exception:  # noqa: BLE001 - SDK/auth payloads stay private at this boundary.
            reason = "error"
            with suppress(Exception):
                await send(
                    "error",
                    message=(
                        "Speech recognition stopped. Check microphone access, service availability "
                        "and service availability, then try again."
                    ),
                )
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with suppress(Exception):
                await adapter.close()
            # SDK stop may finalize the last phrase. Deliver that receipt before
            # closing the socket; never promote unfinalized partial text.
            await asyncio.sleep(0)
            while not adapter.events.empty():
                event = adapter.events.get_nowait()
                if event.get("type") == "final" and not standalone_stop(event["text"]):
                    text = transcript.finalize(event["generation"], event["id"], event["text"])
                    if text:
                        with suppress(Exception):
                            await send(
                                "transcript.final",
                                text=text,
                                id=f"{event['generation']}:{event['id']}",
                            )
            if not adapter.confirmed_closed:
                reason = "error"
            await asyncio.to_thread(meter.finish, confirmed_closed=adapter.confirmed_closed)
            with suppress(Exception):
                await send(
                    "state",
                    state="error" if reason == "error" else "stopped",
                    reason=reason,
                    **meter.snapshot(),
                )
            with suppress(Exception):
                await ws.close(code=1011 if reason == "error" else 1000)
            self.active.pop(stop, None)
            finished.set()


def register_speech_routes(app: FastAPI, settings):
    service = SpeechService(settings)
    app.state.speech = service

    @app.get("/speech/readiness")
    async def readiness(request: Request):
        await authenticate(request, settings)
        return await asyncio.to_thread(speech_readiness, settings)

    @app.websocket("/speech/stream")
    async def stream(ws: WebSocket):
        try:
            browser_guard(ws, settings)
            actor = (
                verify_principal(settings, ws.cookies.get(SESSION_COOKIE, ""))
                if settings.deployment_mode == "production"
                else current_principal(settings)
            )
            if not settings.speech_enabled or not settings.azure_speech_endpoint:
                raise HTTPException(503, "Speech recognition is not configured.")
        except (HTTPException, PermissionError):
            await ws.close(code=1008)
            return
        await ws.accept()
        with principal_scope(actor):
            await service.stream(ws, actor)
