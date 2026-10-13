"""Deterministic provider for explicit TEST_MODE only. No hardware or Azure access."""

import asyncio
import base64
import uuid
from collections.abc import AsyncIterator
from typing import Any

from backend.app.voice.models import VoiceEvent


class FakeVoiceLiveProvider:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[VoiceEvent | None] = asyncio.Queue(maxsize=64)
        self.actions: list[tuple[str, str]] = []
        self.closed = False

    async def emit(self, kind: str, text: str = "", **data: Any) -> None:
        await self.queue.put(VoiceEvent(id=uuid.uuid4().hex, type=kind, text=text, data=data))

    async def create_session(self) -> None:
        await self.emit("ready")
        await self.emit("configured")

    async def send_audio(self, audio: str) -> None:
        self.actions.append(("audio", str(len(audio))))

    async def commit_audio(self) -> None:
        self.actions.append(("commit", ""))

    async def send_text(self, text: str) -> None:
        self.actions.append(("text", text))

    async def respond(self, approved_text: str) -> None:
        response_id = uuid.uuid4().hex
        self.actions.append(("respond", approved_text))
        await self.emit("response.created", response_id=response_id)
        await self.emit("assistant.final", approved_text, response_id=response_id)
        await self.emit(
            "audio", audio=base64.b64encode(bytes(480)).decode(), response_id=response_id
        )
        await self.emit("response.done", response_id=response_id, status="completed")

    async def events(self) -> AsyncIterator[VoiceEvent]:
        while (event := await self.queue.get()) is not None:
            yield event

    async def interrupt(self) -> None:
        self.actions.append(("cancel", ""))

    async def tool_result(self, call_id: str, result: dict[str, Any]) -> None:
        self.actions.append(("tool", call_id))

    async def close_session(self) -> None:
        self.closed = True
        self.actions.append(("close", ""))
