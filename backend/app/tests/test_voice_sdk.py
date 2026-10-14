"""SDK conformance independent of our fake: actual SDK request serializers/events."""

import base64
import json
from unittest.mock import AsyncMock

import pytest
from azure.ai.voicelive.aio import VoiceLiveConnection
from azure.ai.voicelive.models import ServerEvent
from backend.app.voice.azure_voicelive import AzureVoiceLiveProvider
from pydantic import SecretStr


@pytest.mark.asyncio
async def test_actual_sdk_serializes_pcm_vad_commands_and_cancel(settings):
    captured = []

    # Use the installed connection's helpers, not a mock of their signatures.
    class Socket:
        async def send_str(self, value):
            captured.append(json.loads(value))

        async def close(self):
            pass

    socket = Socket()
    connection = VoiceLiveConnection(AsyncMock(), socket)

    class Context:
        async def __aenter__(self):
            return connection

        async def __aexit__(self, *args):
            pass

    options = {}

    def connect(**kwargs):
        options.update(kwargs)
        return Context()

    config = settings.model_copy(
        update={
            "azure_voicelive_endpoint": "https://test.services.ai.azure.com",
            "azure_usage_guard_enabled": True,
            "azure_use_entra_id": False,
            "azure_voicelive_api_key": SecretStr("private-key"),
        }
    )
    provider = AzureVoiceLiveProvider(config, connector=connect)
    await provider.create_session()
    await provider.send_audio(base64.b64encode(bytes(960)).decode())
    await provider.commit_audio()
    await provider.send_text("VPN broken")
    await provider.respond("What error appears?")
    await provider.interrupt()
    await provider.tool_result("call1", {"error": "Write denied"})
    await provider.close_session()
    update = captured[0]["session"]
    assert update["input_audio_format"] == "pcm16" and update["input_audio_sampling_rate"] == 24000
    assert update["output_audio_format"] == "pcm16"
    assert update["turn_detection"]["create_response"] is False
    assert update["turn_detection"]["interrupt_response"] is False
    assert update["input_audio_transcription"]["model"] == "gpt-4o-transcribe"
    assert update["voice"] == {"type": "azure-standard", "name": "en-US-AvaNeural"}
    assert update["tools"] == []
    assert update["max_response_output_tokens"] == 1500
    assert [v["type"] for v in captured] == [
        "session.update",
        "input_audio_buffer.append",
        "input_audio_buffer.commit",
        "conversation.item.create",
        "response.create",
        "response.cancel",
        "conversation.item.create",
    ]
    assert update["tools"] == []
    assert captured[4]["response"] == {
        "pre_generated_assistant_message": {
            "type": "message", "role": "assistant",
            "content": [{"type": "text", "text": "What error appears?"}],
        }
    }
    assert options["api_version"] == "2026-04-10" and options["credential_scopes"] == [
        "https://ai.azure.com/.default"
    ]
    assert options["connection_options"]["max_msg_size"] == 262144
    assert "private-key" not in json.dumps(captured)


@pytest.mark.asyncio
async def test_actual_typed_provider_events_not_fake(settings):
    # Deserialize genuine service event shapes through the SDK's model factory.
    events = [
        {
            "type": "conversation.item.input_audio_transcription.delta",
            "event_id": "e1",
            "item_id": "u1",
            "content_index": 0,
            "delta": "VPN ",
        },
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "event_id": "e2",
            "item_id": "u1",
            "content_index": 0,
            "transcript": "VPN broken",
        },
        {
            "type": "response.created",
            "event_id": "e3",
            "response": {"id": "r1", "status": "in_progress", "output": []},
        },
        {
            "type": "response.audio.delta",
            "event_id": "e4",
            "response_id": "r1",
            "item_id": "a1",
            "output_index": 0,
            "content_index": 0,
            "delta": "AAA=",
        },
        {
            "type": "response.function_call_arguments.done",
            "event_id": "e5",
            "response_id": "r1",
            "item_id": "a1",
            "output_index": 0,
            "call_id": "c1",
            "name": "search_knowledge",
            "arguments": "{}",
        },
        {
            "type": "error",
            "event_id": "e6",
            "error": {
                "type": "server_error",
                "code": "private",
                "message": "private credential payload",
            },
        },
        {
            "type": "response.done", "event_id": "e7",
            "response": {"id": "r1", "status": "cancelled", "output": []},
        },
    ]

    class Connection:
        def __aiter__(self):
            async def iterate():
                for value in events:
                    yield ServerEvent(value)

            return iterate()

    provider = AzureVoiceLiveProvider(settings, credential=object())
    provider.connection = Connection()
    result = [event async for event in provider.events()]
    assert [event.type for event in result] == [
        "user.delta",
        "user.final",
        "response.created",
        "audio",
        "tool",
        "error",
        "response.done",
    ]
    assert result[1].text == "VPN broken" and result[2].data["response_id"] == "r1"
    assert result[3].data["audio"] == "AAA="
    assert "private" not in result[-2].model_dump_json()
    assert result[-1].data["response_id"] == "r1"


@pytest.mark.asyncio
async def test_late_sdk_completion_cannot_clear_a_new_active_response(settings):
    class Connection:
        def __init__(self, identifier):
            self.identifier = identifier
        def __aiter__(self):
            async def iterate():
                yield ServerEvent({"type": "response.done", "event_id": self.identifier,
                    "response": {"id": self.identifier, "status": "cancelled", "output": []}})
            return iterate()
    provider = AzureVoiceLiveProvider(settings, credential=object())
    provider.response_pending = True
    provider.active_response_id = "new-response"
    provider.connection = Connection("old-response")
    assert [event async for event in provider.events()]
    assert provider.response_pending and provider.active_response_id == "new-response"
    provider.connection = Connection("new-response")
    assert [event async for event in provider.events()]
    assert not provider.response_pending and provider.active_response_id == ""


@pytest.mark.asyncio
async def test_connect_failure_sanitized_closed(settings):
    context = AsyncMock()
    context.__aenter__.side_effect = RuntimeError("private credential")
    config = settings.model_copy(
        update={
            "azure_voicelive_endpoint": "https://test.services.ai.azure.com",
            "azure_usage_guard_enabled": True,
        }
    )
    provider = AzureVoiceLiveProvider(
        config, connector=lambda **kwargs: context, credential=object()
    )
    with pytest.raises(RuntimeError, match="connection failed") as error:
        await provider.create_session()
    assert "private" not in str(error.value)
    context.__aexit__.assert_awaited_once()
