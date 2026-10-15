# Continuous speech recognition

DeskMate uses **Azure AI Speech SDK continuous Speech-to-Text only**. It does not request realtime audio-model inference, synthesize a reply, or use browser text-to-speech. Responses remain text. Azure Speech is independent of the selected Groq/Azure OpenAI reasoning provider.

## Browser behavior

Open `http://127.0.0.1:5173`, select Voice, and click **Ready to Talk**. The browser requests microphone permission, captures mono PCM, and streams it to the authenticated backend. The current microphone, mute/unmute, activity meter, transcript panel and Stop control remain available.

Partial recognition replaces the unfinished phrase. Final phrases accumulate in the transcript. A standalone `Stop`, with ordinary trailing punctuation and any letter case, ends recognition without adding that command. Sentences such as “the service will stop responding” remain ordinary text. Pauses and sentence boundaries do not end continuous listening. SDK transport interruptions show **Reconnecting** and resume with a bounded in-memory audio overlap. Final event IDs and the overlap at the reconnect boundary prevent repeated transcript fragments. Unrecoverable authentication/quota errors show **Error**; completed text remains available.

Click **Stop session** or say the standalone command to release the microphone. The backend stops the SDK recognizer and settles connected usage. The browser briefly keeps the WebSocket for a finalized last phrase and usage receipt. If the network fails, local microphone cleanup still happens immediately. You can edit the completed transcript and click **Send message**. This uses the ordinary stateful LangGraph investigation routes and the provider currently selected in the top bar. No LLM sees microphone audio.

The listening timer counts elapsed recording time. Connected Speech seconds and the CAD estimate count the connected SDK interval, including silence and mute. Reconnect backoff is excluded. No short recording-duration or daily-session-count limit is imposed; real service errors, authentication expiry, spending safety and the single-session concurrency rule still apply.

## Configuration and credentials

Set these privately in the existing local configuration, retaining the current usage ledger:

```dotenv
SPEECH_ENABLED=true
AZURE_SPEECH_ENDPOINT=https://<existing-resource-custom-domain>.cognitiveservices.azure.com/
AZURE_SPEECH_LANGUAGE=en-US
AZURE_SPEECH_ESTIMATED_CAD_PER_HOUR=2.0
SPEECH_MAX_CONCURRENT_SESSIONS=1
```

`AZURE_SPEECH_ESTIMATED_CAD_PER_HOUR` is a configurable conservative estimate, not a published price quote. Check the resource's current tier, region and currency before changing it. The application deliberately reserves an estimate even for F0 unless remaining free allowance is independently verified. F0 allowances and concurrency limits are service constraints; [Microsoft's quotas documentation](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-services-quotas-and-limits) and [Speech pricing](https://azure.microsoft.com/en-us/pricing/details/speech/) describe them.

Use a compatible existing Speech or Foundry resource and its custom-domain endpoint. The backend uses an Azure CLI credential on the Mac and a managed/default credential in production. It passes the credential directly to the Speech SDK, which refreshes tokens. Entra authentication requires the custom domain and appropriate resource RBAC; no API key is retrieved or sent to React. See [Microsoft's Speech SDK Entra guide](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-configure-azure-ad-auth).

Changing backend environment configuration requires restarting `make dev`. The LLM selector does not require a restart. No speech personality/system prompt exists: Speech only transcribes. Instructions for answering are the ordinary investigation model prompts.

## Transport and isolation

- `GET /speech/readiness`: authenticated, local configuration and budget inspection only. It does not call Azure or prove live recognition.
- `WS /speech/stream`: allowed browser Origin required. Local mode additionally requires a loopback client and host; production requires a signed, valid application session cookie.
- Browser audio frames: binary, 640 bytes per frame, 16 kHz, mono PCM16LE, 20 ms. The AudioWorklet resamples the browser's actual rate. Muting sends silence; captured muted samples never leave the browser.
- Browser controls: `{"type":"stop"}` and optional `{"type":"ping"}`. The backend rejects invalid frame sizes and accelerated audio uploads.
- Server messages: `state`, `transcript.partial`, `transcript.final`, `usage` and sanitized `error`. No credentials or native SDK diagnostics are returned.

The server holds at most two seconds of PCM in an in-memory reconnect buffer and clears it on close. SDK audio/content logging is never enabled. No audio file is written by the recognition gateway. Text persistence happens only when the user submits a request through the investigation workflow.

The shared persistent usage ledger records `speech_stt` independently from reasoning and historical voice charges. It reserves the next minute before dispatch and renews ahead of connected-time expiry. Confirmed SDK closure settles the reservation to actual connected time. A failed or uncertain shutdown retains its reservation and active flag for operator review. Normal completion never disables the guard. Emergency disablement is checked every second. These application estimates are **not a hard Azure invoice cap**; historical charges and the approved allowance remain intact.

## Tests and explicit live acceptance

Offline tests use temporary local stores and an explicitly injected SDK stand-in:

```bash
.venv/bin/python -m pytest backend/app/tests/test_speech_stt.py -q
npm --prefix frontend test -- src/VoiceSupport.test.tsx src/voiceAudio.test.ts
npm --prefix frontend run typecheck
```

The SDK contract test checks keyless endpoint construction, continuous recognition, PCM format, stop flushing and release. Gateway tests cover multiple finalized sentences, reconnect overlap, standalone Stop, origin rejection, silence accounting, conservative failed shutdown, emergency disablement and immediate subsequent sessions. These tests do not establish live Azure success.

For an explicitly authorized live SDK test, prepare a non-sensitive spoken WAV (mono 16 kHz PCM16LE) and a **new** private report path. The runner never provisions resources, retrieves keys, changes configuration, enables a guard, or calls an LLM:

```bash
PYTHONPATH=. .venv/bin/python scripts/verify_speech.py \
  --run-live-speech \
  --audio /private/tmp/deskmate-speech-acceptance.wav \
  --report /private/tmp/deskmate-speech-result.json
```

Optionally provide `--expected-text-file /private/tmp/expected-speech.txt` to verify a substring without placing transcript text in command arguments. The full recognized text is written only to the new mode-0600 report. Console output contains numeric results and safe status codes. The default timeout is 90 seconds; longer test audio requires `--timeout-seconds` covering its duration plus ten seconds. The runner's bounded acceptance fixture is separate from the unlimited-until-Stop browser feature.

A prerecorded SDK success does not prove physical microphone capture. For browser acceptance, authorize microphone access, speak multiple sentences separated by silence, verify partial/final text, mute/unmute, speak a sentence containing “stop”, then say standalone “Stop”. Verify the microphone indicator turns off, usage stops increasing, completed text can be submitted, and another recording starts. Do not claim this passed unless it was actually observed.

### 2026-10-14 investigation-visibility revision

No Speech transport, recognition, usage-accounting or microphone code changed in the observable-investigation revision. The complete backend suite passed (338 tests, with two unrelated opt-in Aura tests skipped) and all 51 frontend unit tests passed, including the existing voice component/audio coverage. No live Speech SDK or physical microphone check was rerun, so the live acceptance status above is unchanged.
