"""Azure Speech SDK continuous STT over ephemeral browser PCM16LE audio.

No key, token, raw SDK error or audio sample is emitted to the browser or logs.
SDK calls and credential acquisition run outside the FastAPI event loop.
"""

import asyncio
import threading
from collections import deque


def credential_for(settings):
    from backend.app.core.azure_identity import azure_credential

    return azure_credential(settings)


class AzureSpeechRecognizer:
    def __init__(self, settings, *, sdk=None, credential=None):
        self.settings = settings
        self.sdk = sdk
        self.credential = credential
        self.recognizer = None
        self.stream = None
        self.events = asyncio.Queue(maxsize=512)
        self.loop = asyncio.get_running_loop()
        self.generation = 0
        self.closing = False
        self.lock = threading.RLock()
        self.recent_audio = deque(maxlen=100)  # two seconds; never persisted
        self.callbacks = []
        self.confirmed_closed = True

    def emit(self, event):
        if self.closing:
            return

        def enqueue():
            try:
                self.events.put_nowait(event)
            except asyncio.QueueFull:
                # Explicit failure rather than silently losing finalized words.
                while not self.events.empty():
                    self.events.get_nowait()
                self.events.put_nowait({"type": "error", "code": "SPEECH_EVENT_OVERFLOW"})

        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(enqueue)

    def _start(self, replay=False):
        if self.sdk is None:
            import azure.cognitiveservices.speech as speechsdk

            self.sdk = speechsdk
        sdk = self.sdk
        if self.credential is None:
            self.credential = credential_for(self.settings)
        config = sdk.SpeechConfig(
            token_credential=self.credential,
            endpoint=self.settings.azure_speech_endpoint,
        )
        config.speech_recognition_language = self.settings.azure_speech_language
        # Segmentation ends an utterance, never continuous recognition itself.
        config.set_property(sdk.PropertyId.Speech_SegmentationSilenceTimeoutMs, "600")
        config.set_property(sdk.PropertyId.SpeechServiceConnection_InitialSilenceTimeoutMs, "0")
        # Audio/content logging is opt-in in the SDK. Never enable it here.
        fmt = sdk.audio.AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)
        stream = sdk.audio.PushAudioInputStream(stream_format=fmt)
        recognizer = sdk.SpeechRecognizer(
            speech_config=config, audio_config=sdk.audio.AudioConfig(stream=stream)
        )
        self.generation += 1
        generation = self.generation

        def text_event(kind, evt):
            result = evt.result
            if kind == "final" and result.reason != sdk.ResultReason.RecognizedSpeech:
                return
            text = result.text.strip()
            if text:
                self.emit(
                    {"type": kind, "text": text, "id": result.result_id, "generation": generation}
                )

        def canceled(evt):
            code = evt.error_code
            recoverable = code in {
                sdk.CancellationErrorCode.ConnectionFailure,
                sdk.CancellationErrorCode.ServiceTimeout,
                sdk.CancellationErrorCode.ServiceUnavailable,
            }
            self.emit(
                {
                    "type": "interrupted" if recoverable else "error",
                    "code": "SPEECH_CONNECTION_INTERRUPTED"
                    if recoverable
                    else "SPEECH_SERVICE_REJECTED",
                    "generation": generation,
                }
            )

        callbacks = [
            (recognizer.recognizing, lambda evt: text_event("partial", evt)),
            (recognizer.recognized, lambda evt: text_event("final", evt)),
            (recognizer.canceled, canceled),
            (
                recognizer.session_stopped,
                lambda _evt: self.emit({"type": "interrupted", "generation": generation}),
            ),
        ]
        for signal, callback in callbacks:
            signal.connect(callback)
        with self.lock:
            if self.closing:
                stream.close()
                return
            self.stream, self.recognizer, self.callbacks = stream, recognizer, callbacks
            self.confirmed_closed = False
        recognizer.start_continuous_recognition_async().get()
        if replay:
            with self.lock:
                for frame in self.recent_audio:
                    stream.write(frame)

    async def start(self, *, replay=False):
        await asyncio.wait_for(asyncio.to_thread(self._start, replay), 20)

    def write(self, frame):
        if len(frame) != 640:
            raise ValueError("Speech input must be one 20ms PCM16LE frame")
        with self.lock:
            if self.closing:
                return
            self.recent_audio.append(bytes(frame))
            if self.stream is not None:
                self.stream.write(frame)

    def _disconnect(self):
        with self.lock:
            recognizer, stream = self.recognizer, self.stream
            callbacks = self.callbacks
            self.recognizer = self.stream = None
            self.callbacks = []
        if stream:
            stream.close()
        if recognizer:
            recognizer.stop_continuous_recognition_async().get()
        # Successful SDK stop confirms network termination independently of
        # callback cleanup. Python EventSignal supports disconnect_all(), not
        # the JavaScript SDK's individual disconnect(callback) convention.
        self.confirmed_closed = True
        # Final recognition callbacks are flushed by SDK stop. The application
        # stops consuming reconnect notifications before deliberate shutdown.
        for signal, _callback in callbacks:
            signal.disconnect_all()

    async def disconnect(self):
        await asyncio.wait_for(asyncio.to_thread(self._disconnect), 8)

    async def close(self):
        try:
            await self.disconnect()
        finally:
            self.closing = True
            self.recent_audio.clear()
            if self.confirmed_closed and self.credential is not None:
                await asyncio.to_thread(self.credential.close)
                self.credential = None
