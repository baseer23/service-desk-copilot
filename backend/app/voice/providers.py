"""Real-time boundary. Raw audio never traverses MCP or persistent storage."""

from collections.abc import AsyncIterator
from typing import Any, Protocol

from .models import VoiceEvent


class VoiceRealtimeProvider(Protocol):
    async def create_session(self) -> None: ...
    async def send_audio(self, audio: str) -> None: ...
    async def commit_audio(self) -> None: ...
    async def send_text(self, text: str) -> None: ...
    async def respond(self, approved_text: str) -> None: ...
    def events(self) -> AsyncIterator[VoiceEvent]: ...
    async def interrupt(self) -> None: ...
    async def tool_result(self, call_id: str, result: dict[str, Any]) -> None: ...
    async def close_session(self) -> None: ...


class TextSupportTransport:
    """Text-only delivery of graph-approved Azure replies, without speech inference."""

    def __init__(self) -> None:
        import asyncio

        self.queue: asyncio.Queue[VoiceEvent] = asyncio.Queue(maxsize=16)

    async def create_session(self) -> None:
        import uuid

        await self.queue.put(VoiceEvent(id=uuid.uuid4().hex, type="configured"))

    async def send_audio(self, audio: str) -> None:
        raise ValueError("Text transport does not accept audio")

    async def commit_audio(self) -> None:
        raise ValueError("Text transport does not accept audio")

    async def send_text(self, text: str) -> None:
        return None

    async def respond(self, approved_text: str) -> None:
        import uuid

        response_id = uuid.uuid4().hex
        for kind, text, data in [
            ("response.created", "", {"response_id": response_id}),
            ("assistant.final", approved_text, {"response_id": response_id}),
            ("response.done", "", {"response_id": response_id, "status": "completed"}),
        ]:
            await self.queue.put(VoiceEvent(id=uuid.uuid4().hex, type=kind, text=text, data=data))

    async def events(self) -> AsyncIterator[VoiceEvent]:
        while True:
            yield await self.queue.get()

    async def interrupt(self) -> None:
        return None

    async def tool_result(self, call_id: str, result: dict[str, Any]) -> None:
        return None

    async def close_session(self) -> None:
        return None
