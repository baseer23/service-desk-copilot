"""Official async Voice Live SDK adapter; no retry of ambiguous speech operations."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from backend.app.core.azure_errors import azure_error_code
from backend.app.core.azure_usage import AzureUsageGuard
from backend.app.core.config import Settings

from .models import VoiceEvent

PROMPT = (Path(__file__).parent / "prompts/support_v1.md").read_text()


class AzureVoiceLiveProvider:
    def __init__(self, settings: Settings, *, connector: Any = None, credential: Any = None):
        self.settings = settings
        # SDK diagnostics can contain audio/transcripts or raw external error bodies.
        diagnostic_logger = logging.getLogger("azure.ai.voicelive")
        if not any(
            isinstance(handler, logging.NullHandler) for handler in diagnostic_logger.handlers
        ):
            diagnostic_logger.addHandler(logging.NullHandler())
        diagnostic_logger.propagate = False
        self.connector = connector
        self.credential = credential
        self.owns_credential = credential is None
        self.context: Any = None
        self.connection: Any = None
        self.usage_guard = AzureUsageGuard(settings)
        self.usage_token = ""
        self.started = 0.0
        self.watchdog_task: asyncio.Task[Any] | None = None
        self.close_lock = asyncio.Lock()
        self.last_error_code = ""
        self.guard_checked_at = 0.0
        self.guard_check_task: asyncio.Task[None] | None = None
        self.reply_epoch = 0
        self.response_pending = False

    async def create_session(self) -> None:
        from azure.ai.voicelive.aio import connect
        from azure.ai.voicelive.models import (
            AudioInputTranscriptionOptions,
            AzureStandardVoice,
            RequestSession,
            ServerVad,
        )
        from azure.core.credentials import AzureKeyCredential
        from azure.identity.aio import AzureCliCredential, DefaultAzureCredential

        s = self.settings
        if not s.azure_voicelive_endpoint:
            raise RuntimeError("Azure Voice Live endpoint is not configured")
        self.usage_token = self.usage_guard.reserve("session_voice")
        self.started = time.monotonic()
        try:
            self.usage_guard.require_enabled()
            if self.credential is None:
                if s.azure_use_entra_id:
                    self.credential = (
                        AzureCliCredential(process_timeout=5)
                        if s.deployment_mode == "local"
                        else DefaultAzureCredential(
                            process_timeout=5, connection_timeout=5, read_timeout=5, retry_total=0
                        )
                    )
                elif s.azure_voicelive_api_key:
                    self.credential = AzureKeyCredential(
                        s.azure_voicelive_api_key.get_secret_value()
                    )
                else:
                    raise ValueError("An explicit development key is required")
            self.context = (self.connector or connect)(
                endpoint=s.azure_voicelive_endpoint,
                credential=self.credential,
                model=s.azure_voicelive_model,
                api_version=s.azure_voicelive_api_version,
                credential_scopes=["https://ai.azure.com/.default"],
                connection_options={
                    "handshake_timeout": 15,
                    "close_timeout": 5,
                    "heartbeat": 20,
                    "max_msg_size": 262144,
                },
            )
            self.connection = await asyncio.wait_for(self.context.__aenter__(), 20)
            self.ensure_active()
            self.watchdog_task = asyncio.create_task(self.watchdog())
            await self.connection.session.update(
                session=RequestSession(
                    instructions=PROMPT,
                    modalities=["audio", "text"],
                    input_audio_format="pcm16",
                    output_audio_format="pcm16",
                    input_audio_sampling_rate=24000,
                    voice=AzureStandardVoice(name=s.azure_voicelive_voice),
                    input_audio_transcription=AudioInputTranscriptionOptions(
                        model="gpt-4o-transcribe", language="en"
                    ),
                    turn_detection=(
                        None
                        if s.azure_setup_validation_mode
                        else ServerVad(
                            create_response=False, interrupt_response=True, auto_truncate=True
                        )
                    ),
                    tools=[],
                    max_response_output_tokens=s.azure_max_output_tokens,
                )
            )
        except Exception as exc:  # noqa: BLE001 - sanitize external failures at the boundary
            self.last_error_code = azure_error_code(exc)
            await self.close_session()
            raise RuntimeError("Azure Voice Live connection failed") from None

    async def watchdog(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            try:
                await self.ensure_active_async()
            except RuntimeError:
                await self.close_session()
                return

    def ensure_active(self) -> None:
        self.usage_guard.require_enabled()
        self.check_duration()

    def check_duration(self) -> None:
        seconds = (
            self.settings.azure_setup_max_voice_seconds
            if self.settings.azure_setup_validation_mode
            else self.settings.azure_max_voice_session_minutes * 60
        )
        if time.monotonic() - self.started >= seconds:
            raise RuntimeError("Azure voice session duration limit reached")

    async def ensure_active_async(self) -> None:
        if self.settings.data_platform != "azure":
            self.ensure_active()
            return
        if time.monotonic() - self.guard_checked_at >= 0.25:
            if self.guard_check_task is None or self.guard_check_task.done():
                self.guard_check_task = asyncio.create_task(asyncio.to_thread(self.usage_guard.require_enabled))
            await asyncio.shield(self.guard_check_task)
            self.guard_checked_at = time.monotonic()
        self.check_duration()

    async def send_audio(self, audio: str) -> None:
        await self.ensure_active_async()
        await self.connection.input_audio_buffer.append(audio=audio)

    async def commit_audio(self) -> None:
        await self.ensure_active_async()
        await self.connection.input_audio_buffer.commit()

    async def send_text(self, text: str) -> None:
        await self.ensure_active_async()
        if len(text.encode()) > self.settings.azure_max_input_tokens:
            raise RuntimeError("Azure text input limit reached")
        from azure.ai.voicelive.models import InputTextContentPart, UserMessageItem

        await self.connection.conversation.item.create(
            item=UserMessageItem(content=[InputTextContentPart(text=text)])
        )

    async def respond(self, approved_text: str) -> None:
        epoch = self.reply_epoch
        await self.ensure_active_async()
        if epoch != self.reply_epoch or self.connection is None:
            return
        if len(approved_text.encode()) + 128 > self.settings.azure_max_input_tokens:
            raise RuntimeError("Azure voice reply input limit reached")
        self.response_pending = True
        await self.connection.response.create(
            response={
                "instructions": "Read only this approved reply verbatim. Add no information: "
                + approved_text,
                "tool_choice": "none",
                "modalities": ["audio", "text"],
                "max_output_tokens": self.settings.azure_max_output_tokens,
                "input_items": [],
                "commit": False,
            }
        )

    async def interrupt(self) -> None:
        self.reply_epoch += 1
        if self.response_pending and self.connection:
            await self.connection.response.cancel()
            self.response_pending = False

    async def tool_result(self, call_id: str, result: dict[str, Any]) -> None:
        import json

        from azure.ai.voicelive.models import FunctionCallOutputItem

        await self.connection.conversation.item.create(
            item=FunctionCallOutputItem(call_id=call_id, output=json.dumps(result))
        )

    async def events(self) -> AsyncIterator[VoiceEvent]:
        async for event in self.connection:
            value = event.as_dict()
            kind = value.get("type", "")
            types = {
                "session.created": "ready",
                "session.updated": "configured",
                "conversation.item.input_audio_transcription.delta": "user.delta",
                "conversation.item.input_audio_transcription.completed": "user.final",
                "conversation.item.input_audio_transcription.failed": "error",
                "input_audio_buffer.speech_started": "speech.started",
                "input_audio_buffer.speech_stopped": "speech.stopped",
                "input_audio_buffer.committed": "audio.committed",
                "response.created": "response.created",
                "response.done": "response.done",
                "response.text.delta": "assistant.delta",
                "response.text.done": "assistant.final",
                "response.audio_transcript.delta": "assistant.delta",
                "response.audio_transcript.done": "assistant.final",
                "response.audio.delta": "audio",
                "response.function_call_arguments.done": "tool",
                "error": "error",
            }
            if kind not in types:
                continue  # Includes private reasoning events.
            if kind == "error":
                detail = value.get("error", {})
                from types import SimpleNamespace

                self.last_error_code = azure_error_code(SimpleNamespace(body=detail))
            data = {
                key: value[key]
                for key in ("item_id", "response_id", "call_id", "name", "arguments")
                if key in value
            }
            if kind in {
                "response.text.delta",
                "response.text.done",
                "response.audio_transcript.delta",
                "response.audio_transcript.done",
            }:
                data["channel"] = "audio_transcript" if "audio_transcript" in kind else "text"
            if kind == "response.created":
                data["response_id"] = value.get("response", {}).get("id", "")
            if kind == "response.done":
                self.response_pending = False
                data["status"] = value.get("response", {}).get("status", "")
                if self.usage_token:
                    self.usage_guard.voice_usage(
                        self.usage_token,
                        value.get("event_id", "") or value.get("response", {}).get("id", ""),
                        value.get("response", {}).get("usage"),
                    )
            if types[kind] == "audio":
                data["audio"] = value.get("delta", "")
            text = (
                value.get("transcript", value.get("text", value.get("delta", "")))
                if types[kind] in {"user.delta", "user.final", "assistant.delta", "assistant.final"}
                else ""
            )
            yield VoiceEvent(
                id=value.get("event_id", ""), type=types[kind], text=text[:8000], data=data
            )

    async def close_session(self) -> None:
        self.reply_epoch += 1
        self.response_pending = False
        if self.guard_check_task:
            self.guard_check_task.cancel()
            await asyncio.gather(self.guard_check_task, return_exceptions=True)
            self.guard_check_task = None
        if self.watchdog_task and self.watchdog_task is not asyncio.current_task():
            self.watchdog_task.cancel()
            await asyncio.gather(self.watchdog_task, return_exceptions=True)
        self.watchdog_task = None
        async with self.close_lock:
            await self._close_session()

    async def _close_session(self) -> None:
        if self.context:
            try:
                await asyncio.wait_for(self.context.__aexit__(None, None, None), 5)
            except Exception:  # noqa: BLE001, S110
                pass  # Close is best-effort; no raw SDK errors in logs.
            self.context = None
        if self.owns_credential and self.credential and hasattr(self.credential, "close"):
            await self.credential.close()
            self.credential = None
        self.connection = None
        if self.usage_token:
            token, self.usage_token = self.usage_token, ""
            self.usage_guard.finish(token, seconds=time.monotonic() - self.started)
            if self.settings.azure_live_test_mode and not self.settings.azure_setup_validation_mode:
                self.usage_guard.disable()
