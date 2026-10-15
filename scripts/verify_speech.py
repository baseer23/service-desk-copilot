"""Explicit real Speech SDK acceptance with prerecorded PCM; never runs on startup.

Run with PYTHONPATH=. .venv/bin/python scripts/verify_speech.py --help.
The full transcript is written only to a new private report file. No LLM request,
resource provisioning, microphone access, cloud persistence, key retrieval or
policy mutation is performed.
"""

import argparse
import asyncio
import json
import os
import time
import wave
from pathlib import Path

from backend.app.core.config import get_settings
from backend.app.speech.azure_stt import AzureSpeechRecognizer
from backend.app.speech.transcript import Transcript, standalone_stop
from backend.app.speech.usage import SpeechMeter


def read_audio(path):
    with wave.open(str(path), "rb") as audio:
        if (
            audio.getnchannels(),
            audio.getsampwidth(),
            audio.getframerate(),
            audio.getcomptype(),
        ) != (1, 2, 16000, "NONE"):
            raise ValueError("Expected mono 16kHz PCM16LE WAV")
        seconds = audio.getnframes() / 16000
        if not 0 < seconds <= 300:
            raise ValueError("Acceptance audio must be between zero and 300 seconds")
        data = audio.readframes(audio.getnframes())
        if len(data) != audio.getnframes() * 2 or not any(data):
            raise ValueError("Acceptance audio is silent")
        return data, seconds


async def verify(settings, data, seconds, expected, timeout):
    adapter = AzureSpeechRecognizer(settings)
    meter = SpeechMeter(settings)
    transcript = Transcript()
    finals = []
    stop = asyncio.Event()
    started = time.monotonic()
    first_partial = None
    first_final = None
    error = None

    async def feed():
        for offset in range(0, len(data), 640):
            frame = data[offset : offset + 640].ljust(640, b"\0")
            await asyncio.to_thread(adapter.write, frame)
            await asyncio.sleep(0.02)
        # Explicit trailing silence allows the final utterance to segment.
        for _ in range(100):
            await asyncio.to_thread(adapter.write, bytes(640))
            await asyncio.sleep(0.02)
        await asyncio.sleep(3)
        stop.set()

    async def events():
        nonlocal first_partial, first_final
        while not stop.is_set():
            event = await adapter.events.get()
            if event["type"] == "partial" and first_partial is None:
                first_partial = round(time.monotonic() - started, 3)
            elif event["type"] == "final":
                if standalone_stop(event["text"]):
                    stop.set()
                    return
                text = transcript.finalize(event["generation"], event["id"], event["text"])
                if text:
                    first_final = (
                        first_final
                        if first_final is not None
                        else round(time.monotonic() - started, 3)
                    )
                    finals.append(text)
            elif event["type"] in {"error", "interrupted"}:
                raise RuntimeError("Real acceptance recognition was interrupted")

    async def heartbeat():
        while not stop.is_set():
            await asyncio.to_thread(meter.heartbeat)
            await asyncio.sleep(1)

    tasks = []
    try:
        if not settings.speech_enabled or not settings.azure_speech_endpoint:
            raise ValueError("Speech is not configured")
        await asyncio.to_thread(meter.start)
        meter.connected()
        await adapter.start()
        tasks = [asyncio.create_task(fn()) for fn in (feed, events, heartbeat)]
        stopped = asyncio.create_task(stop.wait())
        tasks.append(stopped)
        done, _ = await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if not done:
            raise TimeoutError
        for task in done:
            task.result()
    except Exception:  # noqa: BLE001 - Acceptance reports never expose native diagnostics.
        error = "SPEECH_ACCEPTANCE_FAILED"
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await adapter.close()
        except Exception:  # noqa: BLE001 - Acceptance reports never expose native diagnostics.
            error = "SPEECH_CLOSE_UNCONFIRMED"
        # Native stop flushes any last finalized phrase before releasing resources.
        await asyncio.sleep(0)
        while not adapter.events.empty():
            event = adapter.events.get_nowait()
            if event["type"] == "final" and not standalone_stop(event["text"]):
                text = transcript.finalize(event["generation"], event["id"], event["text"])
                if text:
                    finals.append(text)
        try:
            await asyncio.to_thread(meter.finish, confirmed_closed=adapter.confirmed_closed)
        except Exception:  # noqa: BLE001 - Acceptance reports never expose native diagnostics.
            error = "SPEECH_USAGE_SETTLEMENT_FAILED"
    full = " ".join(finals)
    expected_match = not expected or expected.casefold().strip() in full.casefold()
    report = {
        "passed": error is None and bool(finals) and expected_match,
        "error": error,
        "recognized_text": full,
        "recognized_final_count": len(finals),
        "expected_matched": expected_match,
        "input_audio_seconds": round(seconds, 3),
        "first_partial_seconds": first_partial,
        "first_final_seconds": first_final,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "sdk_closed": adapter.confirmed_closed,
        **meter.snapshot(),
        "input_source": "prerecorded_wav",
        "physical_microphone_tested": False,
    }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-live-speech",
        action="store_true",
        help="Explicitly authorize one metered live STT request",
    )
    parser.add_argument(
        "--audio", type=Path, required=True, help="Existing non-sensitive mono16kPCM16 WAV"
    )
    parser.add_argument(
        "--report", type=Path, required=True, help="New private JSON report path; never overwritten"
    )
    parser.add_argument(
        "--expected-text-file",
        type=Path,
        help="Optional private file containing expected transcription substring",
    )
    parser.add_argument("--timeout-seconds", type=float, default=90)
    args = parser.parse_args()
    if not args.run_live_speech:
        parser.error(
            "No live request made. Add --run-live-speech only with authorized Azure usage."
        )
    try:
        data, seconds = read_audio(args.audio)
        if not seconds + 10 <= args.timeout_seconds <= 600:
            raise ValueError(
                "Acceptance timeout must cover audio plus ten seconds and be at most 600 seconds"
            )
        expected = args.expected_text_file.read_text() if args.expected_text_file else ""
        # Create the report first so an invalid path cannot waste a paid request.
        args.report.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except (ValueError, OSError, wave.Error):
        print(json.dumps({"passed": False, "error": "INVALID_PRIVATE_ACCEPTANCE_INPUT"}))
        return 2
    with os.fdopen(fd, "w") as output:
        try:
            report = asyncio.run(
                verify(get_settings(), data, seconds, expected, args.timeout_seconds)
            )
        except Exception:  # noqa: BLE001 - Configuration diagnostics can contain private values.
            report = {"passed": False, "error": "SPEECH_CONFIGURATION_INVALID"}
        json.dump(report, output, indent=2)
        output.write("\n")
    public = {key: value for key, value in report.items() if key != "recognized_text"}
    print(json.dumps(public))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
