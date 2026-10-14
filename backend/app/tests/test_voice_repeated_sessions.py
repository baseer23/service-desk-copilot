"""Exercise automatic termination through HTTP/WebSocket and real local MCP/state."""

import pytest
from backend.app.voice.service import VoiceRuntime


@pytest.mark.parametrize("record_store", [False, True])
def test_three_consecutive_auto_stops_are_normal_and_allow_new_session(
    make_client, monkeypatch, record_store
):
    monkeypatch.setattr(VoiceRuntime, "session_limit", lambda self: 0.3)
    client = make_client(
        {
            "VOICE_ENABLED": "true",
            "VOICE_PROVIDER": "fake",
            "LOCAL_PERSISTENCE_ENABLED": str(record_store).lower(),
            "VOICE_TRANSCRIPT_RETENTION_HOURS": "0",
            "VOICE_SESSION_SIGNING_KEY": "isolated-repeat-tests-key-xxxxxxxxxxxxxxxxxxx",
        }
    )
    origin = {"Origin": "http://testserver"}
    for _ in range(3):
        response = client.post("/voice/sessions", json={}, headers=origin)
        assert response.status_code == 201, response.text
        sid = response.json()["session_id"]
        terminal, cleared = None, False
        with client.websocket_connect("/voice/sessions/" + sid + "/stream", headers=origin) as ws:
            while terminal is None:
                event = ws.receive_json()
                assert event["type"] != "error"
                if event["type"] == "playback.clear":
                    cleared = True
                if (
                    event["type"] == "session.snapshot"
                    and event["payload"]["conversation"]["status"] == "stopped"
                ):
                    terminal = event["payload"]["conversation"]
        assert cleared and terminal["failure_category"] == "duration_limit"
        assert client.get("/voice/sessions/" + sid).json()["status"] == "stopped"


@pytest.mark.asyncio
async def test_cancelling_sender_finishes_inflight_envelope_before_terminal_state():
    import asyncio
    from types import SimpleNamespace

    runtime = object.__new__(VoiceRuntime)
    runtime.sequence = 0
    runtime.session_id = "fictional-session"
    runtime.socket_lock = asyncio.Lock()
    entered, released = asyncio.Event(), asyncio.Event()
    received = []

    async def send(value):
        entered.set()
        await released.wait()
        received.append(value)

    runtime.socket = SimpleNamespace(send_json=send)
    runtime.outgoing = asyncio.Queue()
    await runtime.outgoing.put(("audio", {"audio": "AAA="}))
    sender = asyncio.create_task(runtime.sender())
    await entered.wait()
    sender.cancel()
    released.set()
    with pytest.raises(asyncio.CancelledError):
        await sender
    await runtime.send_envelope("playback.clear", {})
    assert [event["sequence"] for event in received] == [1, 2]
    assert runtime.sequence == 2
