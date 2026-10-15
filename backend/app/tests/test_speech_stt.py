"""Isolated Speech SDK contract, transcript and local billing regressions."""

import asyncio
from types import SimpleNamespace

import pytest
from backend.app.core.azure_usage import AzureUsageBlocked, AzureUsageGuard
from backend.app.core.config import Settings
from backend.app.speech.api import register_speech_routes
from backend.app.speech.azure_stt import AzureSpeechRecognizer
from backend.app.speech.transcript import Transcript, standalone_stop
from backend.app.speech.usage import SpeechMeter
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def settings(tmp_path, **changes):
    return Settings(
        _env_file=None,
        test_mode=True,
        llm_provider="stub",
        embedding_provider="stub",
        case_db_path=tmp_path / "cases.sqlite",
        chroma_dir=tmp_path / "chroma",
        azure_usage_guard_enabled=True,
        azure_emergency_disable=False,
        azure_estimated_monthly_app_limit_cad=155,
        azure_speech_estimated_cad_per_hour=2,
        speech_enabled=True,
        azure_speech_endpoint="https://example.cognitiveservices.azure.com/",
        **changes,
    )


@pytest.mark.parametrize("text", ["Stop", " stop. ", "STOP!", "Stop?", "Stop,", "Stop;"])
def test_standalone_stop_is_excluded(text):
    assert standalone_stop(text)


@pytest.mark.parametrize(
    "text",
    ["stop listening", "don't stop", "the printer stopped", "stop the service", "Stop now", ""],
)
def test_stop_inside_sentences_remains_recognized_text(text):
    assert not standalone_stop(text)


def test_final_duplicates_and_reconnect_overlap_do_not_duplicate_transcript():
    transcript = Transcript()
    assert transcript.accept(1, "id-1", "VPN fails.")
    assert not transcript.accept(1, "id-1", "VPN fails.")
    assert not transcript.accept(2, "id-2", "VPN fails")
    assert transcript.accept(2, "id-3", "VPN fails")  # deliberate repeat in same SDK session
    assert transcript.accept(3, "id-4", "Different final words")


def test_speech_meter_counts_silence_reserves_ahead_and_preserves_history(tmp_path):
    s = settings(tmp_path)
    guard = AzureUsageGuard(s)
    guard.enable_demo(155, "isolated-test")
    with guard.transaction() as db:
        db.execute(
            "INSERT INTO azure_usage(id,month,day,kind,estimate,active) "
            "VALUES('historical','2026-01','2026-01-01','session_voice',500000,0)"
        )
    clock = [0.0]
    meter = SpeechMeter(s, guard, clock=lambda: clock[0])
    meter.start()
    meter.connected()
    clock[0] = 56
    meter.heartbeat()
    assert meter.reserved_seconds == 120
    clock[0] = 90
    meter.disconnected()
    clock[0] = 100  # reconnect backoff is not connected billable time
    meter.connected()
    clock[0] = 130
    meter.finish(confirmed_closed=True)
    assert meter.seconds == 120
    with guard.inspection() as db:
        assert db.execute(
            "SELECT seconds,estimate,active FROM azure_usage WHERE id=?", (meter.id,)
        ).fetchone() == (120, 66667, 0)
        assert (
            db.execute("SELECT estimate FROM azure_usage WHERE id='historical'").fetchone()[0]
            == 500000
        )
    assert guard.snapshot()["enabled"]
    second = SpeechMeter(s, guard)
    second.start()
    second.finish(confirmed_closed=True)


def test_unconfirmed_sdk_close_retains_budget_and_concurrency_guard(tmp_path):
    s = settings(tmp_path)
    guard = AzureUsageGuard(s)
    guard.enable_demo(155, "isolated-test")
    meter = SpeechMeter(s, guard)
    meter.start()
    meter.connected()
    meter.finish(confirmed_closed=False)
    with guard.inspection() as db:
        assert (
            db.execute("SELECT active FROM azure_usage WHERE id=?", (meter.id,)).fetchone()[0] == 1
        )
    with pytest.raises(AzureUsageBlocked, match="still active"):
        SpeechMeter(s, guard).start()


def test_emergency_stop_is_not_cleared_by_speech_completion(tmp_path):
    s = settings(tmp_path)
    guard = AzureUsageGuard(s)
    guard.enable_demo(155, "isolated-test")
    meter = SpeechMeter(s, guard)
    meter.start()
    meter.connected()
    guard.disable()
    with pytest.raises(AzureUsageBlocked):
        meter.heartbeat()
    meter.finish(confirmed_closed=True)
    assert not guard.snapshot()["enabled"]


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, fn):
        self.callbacks.append(fn)

    def disconnect_all(self):
        self.callbacks.clear()

    def emit(self, value):
        for fn in list(self.callbacks):
            fn(value)


@pytest.mark.asyncio
async def test_actual_sdk_adapter_contract_is_continuous_keyless_pcm_and_stop_flush(tmp_path):
    made = {}

    class Future:
        def get(self):
            return None

    class Config:
        def __init__(self, **kwargs):
            made["config"] = kwargs
            self.props = {}

        def set_property(self, key, value):
            self.props[key] = value

    class Stream:
        def __init__(self, **kwargs):
            made["stream"] = self
            made["format"] = kwargs

        def write(self, data):
            made.setdefault("frames", []).append(data)

        def close(self):
            made["closed"] = True

    class Recognizer:
        def __init__(self, **kwargs):
            made["recognizer"] = self
            for name in ("recognizing", "recognized", "canceled", "session_stopped"):
                setattr(self, name, Signal())

        def start_continuous_recognition_async(self):
            made["continuous"] = True
            return Future()

        def stop_continuous_recognition_async(self):
            self.recognized.emit(
                SimpleNamespace(
                    result=SimpleNamespace(reason="recognized", text="last final", result_id="last")
                )
            )
            made["stopped"] = True
            return Future()

    sdk = SimpleNamespace(
        SpeechConfig=Config,
        SpeechRecognizer=Recognizer,
        PropertyId=SimpleNamespace(
            Speech_SegmentationSilenceTimeoutMs="segment",
            SpeechServiceConnection_InitialSilenceTimeoutMs="initial",
        ),
        ResultReason=SimpleNamespace(RecognizedSpeech="recognized"),
        CancellationErrorCode=SimpleNamespace(
            ConnectionFailure=1, ServiceTimeout=2, ServiceUnavailable=3
        ),
        audio=SimpleNamespace(
            PushAudioInputStream=Stream,
            AudioStreamFormat=lambda **kwargs: kwargs,
            AudioConfig=lambda **kwargs: kwargs,
        ),
    )
    credential = SimpleNamespace(close=lambda: made.update(credential_closed=True))
    adapter = AzureSpeechRecognizer(settings(tmp_path), sdk=sdk, credential=credential)
    await adapter.start()
    assert made["config"]["token_credential"] is credential
    assert "subscription" not in made["config"]
    assert made["format"]["stream_format"] == {
        "samples_per_second": 16000,
        "bits_per_sample": 16,
        "channels": 1,
    }
    assert made["continuous"]
    await asyncio.to_thread(adapter.write, bytes(640))
    with pytest.raises(ValueError):
        adapter.write(bytes(960))
    await adapter.close()
    await asyncio.sleep(0)
    assert made["stopped"] and made["closed"] and made["credential_closed"]
    assert (await adapter.events.get())["text"] == "last final"
    assert adapter.confirmed_closed and not adapter.recent_audio


def test_speech_websocket_rejects_foreign_origin_before_sdk_or_credentials(tmp_path):
    app = FastAPI()
    register_speech_routes(app, settings(tmp_path))
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect(
                "/speech/stream", headers={"origin": "https://untrusted.invalid"}
            ):
                pass
        assert rejected.value.code == 1008


class GatewayRecognizer:
    def __init__(self, _settings):
        self.events = asyncio.Queue()
        self.generation = 0
        self.confirmed_closed = True
        self.frames = 0

    async def start(self, *, replay=False):
        self.generation += 1
        self.confirmed_closed = False
        if replay:
            await self.events.put(
                {
                    "type": "final",
                    "generation": self.generation,
                    "id": "replay",
                    "text": "VPN fails.",
                }
            )

    def write(self, _frame):
        self.frames += 1
        if self.frames == 1:
            self.events.put_nowait(
                {
                    "type": "final",
                    "generation": self.generation,
                    "id": "first",
                    "text": "VPN fails.",
                }
            )
        elif self.frames == 2:
            self.events.put_nowait({"type": "interrupted", "generation": self.generation})
        elif self.frames == 3:
            self.events.put_nowait(
                {
                    "type": "final",
                    "generation": self.generation,
                    "id": "second",
                    "text": "Authentication fails.",
                }
            )

    async def disconnect(self):
        self.confirmed_closed = True

    async def close(self):
        self.confirmed_closed = True


def receive_until(ws, predicate, seen=None):
    for _ in range(30):
        value = ws.receive_json()
        if seen is not None:
            seen.append(value)
        if predicate(value):
            return value
    raise AssertionError("Expected speech event did not arrive")


def test_gateway_continues_after_sentences_recovers_and_settles_actual_usage(tmp_path):
    s = settings(tmp_path)
    guard = AzureUsageGuard(s)
    guard.enable_demo(155, "isolated-test")
    app = FastAPI()
    register_speech_routes(app, s)
    app.state.speech.recognizer_factory = GatewayRecognizer
    with TestClient(app) as client:
        assert client.get("/speech/readiness").json()["ready"]
        with client.websocket_connect(
            "/speech/stream", headers={"origin": "http://127.0.0.1:5173"}
        ) as ws:
            receive_until(ws, lambda v: v.get("state") == "listening")
            ws.send_bytes(bytes(640))
            assert (
                receive_until(ws, lambda v: v["type"] == "transcript.final")["text"] == "VPN fails."
            )
            ws.send_bytes(bytes(640))
            receive_until(ws, lambda v: v.get("state") == "reconnecting")
            observed = []
            receive_until(ws, lambda v: v.get("state") == "listening", observed)
            ws.send_bytes(bytes(640))
            assert (
                receive_until(ws, lambda v: v["type"] == "transcript.final", observed)["text"]
                == "Authentication fails."
            )
            assert not any(v.get("id") == "2:replay" for v in observed)
            ws.send_json({"type": "stop"})
            assert (
                receive_until(ws, lambda v: v.get("state") == "stopped")["reason"]
                == "explicit_stop"
            )
    with guard.inspection() as db:
        rows = db.execute(
            "SELECT active,seconds,estimate FROM azure_usage WHERE kind='speech_stt'"
        ).fetchall()
        assert len(rows) == 1 and rows[0][0] == 0 and rows[0][1] > 0
    assert guard.snapshot()["enabled"]


def test_gateway_spoken_stop_never_becomes_transcript(tmp_path):
    class StopRecognizer(GatewayRecognizer):
        def write(self, _frame):
            self.events.put_nowait(
                {"type": "final", "generation": self.generation, "id": "stop", "text": "Stop."}
            )

    s = settings(tmp_path)
    guard = AzureUsageGuard(s)
    guard.enable_demo(155, "isolated-test")
    app = FastAPI()
    register_speech_routes(app, s)
    app.state.speech.recognizer_factory = StopRecognizer
    with TestClient(app) as client:
        with client.websocket_connect(
            "/speech/stream", headers={"origin": "http://127.0.0.1:5173"}
        ) as ws:
            receive_until(ws, lambda v: v.get("state") == "listening")
            ws.send_bytes(bytes(640))
            observed = []
            assert (
                receive_until(ws, lambda v: v.get("state") == "stopped", observed)["reason"]
                == "spoken_stop"
            )
            assert not any(v["type"].startswith("transcript.") for v in observed)
    assert guard.snapshot()["enabled"]


def test_reconnect_replay_suffix_is_removed_without_losing_new_words():
    transcript = Transcript()
    assert (
        transcript.finalize(1, "first", "The VPN authentication fails.")
        == "The VPN authentication fails."
    )
    assert (
        transcript.finalize(2, "replay", "Authentication fails. Outlook also fails.")
        == "Outlook also fails."
    )
    assert transcript.finalize(2, "repeat", "Outlook also fails.") == "Outlook also fails."


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.cognitiveservices.azure.com/",
        "https://example.cognitiveservices.azure.com/recognize",
        "https://example.cognitiveservices.azure.com/?token=secret",
        "https://example.cognitiveservices.azure.com:8443/",
        "https://example.services.ai.azure.com/",
        "https://example.cognitiveservices.azure.com.attacker.invalid/",
        "https://user:secret@example.cognitiveservices.azure.com/",
    ],
)
def test_speech_endpoint_is_a_keyless_custom_domain_origin(tmp_path, endpoint):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(_env_file=None, azure_speech_endpoint=endpoint)


def test_speech_meter_preserves_separate_setup_spending_cap(tmp_path):
    s = settings(tmp_path, azure_setup_validation_mode=True, azure_estimated_setup_limit_cad=0.04)
    guard = AzureUsageGuard(s)
    guard.enable_demo(155, "isolated-test")
    clock = [0.0]
    meter = SpeechMeter(s, guard, clock=lambda: clock[0])
    meter.start()
    meter.connected()
    clock[0] = 56
    with pytest.raises(AzureUsageBlocked, match="setup allowance"):
        meter.heartbeat()
    meter.finish(confirmed_closed=True)
    with guard.inspection() as db:
        assert (
            db.execute("SELECT phase FROM azure_usage WHERE id=?", (meter.id,)).fetchone()[0]
            == "setup"
        )


@pytest.mark.asyncio
async def test_installed_native_speech_sdk_signal_shutdown_without_connecting(
    tmp_path, monkeypatch
):
    """Exercise real native EventSignal API: invented fake disconnect() hid a bug.

    Construct a recognizer without starting it. Stop on an unopened native
    recognizer never dispatches recognition or retrieves an Azure credential.
    """
    import azure.cognitiveservices.speech as sdk

    def forbidden_start(*_args, **_kwargs):
        pytest.fail("An offline SDK lifecycle test must never start recognition")

    monkeypatch.setattr(sdk.SpeechRecognizer, "start_continuous_recognition_async", forbidden_start)
    config = sdk.SpeechConfig(
        subscription="isolated-offline-placeholder",
        endpoint="https://example.cognitiveservices.azure.com/",
    )
    stream = sdk.audio.PushAudioInputStream(
        stream_format=sdk.audio.AudioStreamFormat(
            samples_per_second=16000, bits_per_sample=16, channels=1
        )
    )
    recognizer = sdk.SpeechRecognizer(
        speech_config=config, audio_config=sdk.audio.AudioConfig(stream=stream)
    )
    adapter = AzureSpeechRecognizer(settings(tmp_path))
    adapter.recognizer = recognizer
    adapter.stream = stream
    adapter.confirmed_closed = False

    def callback(_event):
        return None

    for signal in (
        recognizer.recognizing,
        recognizer.recognized,
        recognizer.canceled,
        recognizer.session_stopped,
    ):
        signal.connect(callback)
        adapter.callbacks.append((signal, callback))
    await adapter.close()
    assert adapter.confirmed_closed and adapter.recognizer is None and adapter.stream is None
    assert not adapter.callbacks


def test_initialization_failure_never_leaks_details_or_leaves_active_shutdown_waiter(tmp_path):
    def unavailable(_settings):
        raise OSError("private database identifier and credential details")

    s = settings(tmp_path)
    app = FastAPI()
    register_speech_routes(app, s)
    app.state.speech.meter_factory = unavailable
    with TestClient(app) as client:
        with client.websocket_connect(
            "/speech/stream", headers={"origin": "http://127.0.0.1:5173"}
        ) as ws:
            response = ws.receive_json()
            assert response["type"] == "error" and "private" not in response["message"]
    assert not app.state.speech.active
