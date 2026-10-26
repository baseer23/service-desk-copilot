# Continuous speech recognition

DeskMate uses **Azure AI Speech SDK continuous Speech-to-Text only**. It does not request realtime audio-model inference, synthesize a reply, or use browser text-to-speech. Responses remain text. Azure Speech is independent of the selected Groq/Azure OpenAI reasoning provider.

## Browser behavior

Open `http://127.0.0.1:5173`, select Voice, and click **Ready to Talk**. The browser requests microphone permission, captures mono PCM, and streams it to the authenticated backend. The current microphone, mute/unmute, activity meter, transcript panel and Stop control remain available.

Partial recognition replaces the unfinished phrase. Final phrases accumulate in the transcript. A standalone `Stop`, with ordinary trailing punctuation and any letter case, ends recognition without adding that command. Sentences such as “the service will stop responding” remain ordinary text. Pauses and sentence boundaries do not end continuous listening. SDK transport interruptions show **Reconnecting** and resume with a bounded in-memory audio overlap. Final event IDs and the overlap at the reconnect boundary prevent repeated transcript fragments. Unrecoverable authentication/quota errors show **Error**; completed text remains available.

Click **Stop session** or say the standalone command to release the microphone. The backend stops the SDK recognizer and settles connected usage. The browser briefly keeps the WebSocket for a finalized last phrase and usage receipt. If the network fails, local microphone cleanup still happens immediately. You can edit the completed transcript and click **Send message**. This uses the ordinary stateful LangGraph investigation routes and the provider currently selected in the top bar. No LLM sees microphone audio.

The listening timer counts elapsed recording time. Connected Speech seconds cover the connected SDK interval, including silence and mute. Reconnect backoff is excluded. No short recording-duration or daily-session-count limit is imposed; real service errors, authentication expiry and the single-session concurrency rule still apply.

## Configuration and credentials

Set these privately in the existing local configuration:

```dotenv
SPEECH_ENABLED=true
AZURE_SPEECH_ENDPOINT=https://<existing-resource-custom-domain>.cognitiveservices.azure.com/
AZURE_SPEECH_LANGUAGE=en-US
SPEECH_MAX_CONCURRENT_SESSIONS=1
```

Speech billing, quotas and concurrency are controlled by Azure. DeskMate neither estimates charges nor reserves or blocks application budget. [Microsoft's quotas documentation](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-services-quotas-and-limits) and [Speech pricing](https://azure.microsoft.com/en-us/pricing/details/speech/) describe the provider limits.

Use a compatible existing Speech or Foundry resource and its custom-domain endpoint. The backend uses an Azure CLI credential on the Mac and a managed/default credential in production. It passes the credential directly to the Speech SDK, which refreshes tokens. Entra authentication requires the custom domain and appropriate resource RBAC; no API key is retrieved or sent to React. See [Microsoft's Speech SDK Entra guide](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-configure-azure-ad-auth).

Changing backend environment configuration requires restarting `make dev`. The LLM selector does not require a restart. No speech personality/system prompt exists: Speech only transcribes. Instructions for answering are the ordinary investigation model prompts.

## Transport and isolation

- `GET /speech/readiness`: authenticated, local configuration and budget inspection only. It does not call Azure or prove live recognition.
- `WS /speech/stream`: allowed browser Origin required. Local mode additionally requires a loopback client and host; production requires a signed, valid application session cookie.
- Browser audio frames: binary, 640 bytes per frame, 16 kHz, mono PCM16LE, 20 ms. The AudioWorklet resamples the browser's actual rate. Muting sends silence; captured muted samples never leave the browser.
- Browser controls: `{"type":"stop"}` and optional `{"type":"ping"}`. The backend rejects invalid frame sizes and accelerated audio uploads.
- Server messages: `state`, `transcript.partial`, `transcript.final`, `usage` and sanitized `error`. No credentials or native SDK diagnostics are returned.

The server holds at most two seconds of PCM in an in-memory reconnect buffer and clears it on close. SDK audio/content logging is never enabled. No audio file is written by the recognition gateway. Text persistence happens only when the user submits a request through the investigation workflow.

DeskMate records connected recognition time in memory for session diagnostics. It does not persist a cost ledger, make reservations, or reject a session based on an internal allowance. Confirmed SDK closure ends the local meter; Azure remains responsible for usage accounting.

## Tests and explicit live acceptance

Offline tests use temporary local stores and an explicitly injected SDK stand-in:

```bash
.venv/bin/python -m pytest backend/app/tests/test_speech_stt.py -q
npm --prefix frontend test -- src/VoiceSupport.test.tsx src/voiceAudio.test.ts
npm --prefix frontend run typecheck
```

The SDK contract test checks keyless endpoint construction, continuous recognition, PCM format, stop flushing and release. Gateway tests cover multiple finalized sentences, reconnect overlap, standalone Stop, origin rejection, silence accounting and immediate subsequent sessions. These tests do not establish live Azure success.

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

### 2026-10-14 document-evidence revision

The document extraction/indexing changes do not alter Speech transport, transcript handling or microphone behavior. DeskMate does not reserve or cost-account Speech sessions. No live Speech SDK or physical microphone check was run; the live acceptance status above is unchanged.

### 2026-10-15 Azure OpenAI diagnostics revision

The reasoning-provider error and investigation-timeline changes do not alter Speech SDK transport, continuous recognition, transcript ownership or microphone behavior. No live Speech request or physical microphone check was run; prior Speech acceptance status remains unchanged.

### 2026-10-16 conversation-runtime verification

The generic conversation refactor did not change the Speech SDK lifecycle. Browser tests continue to verify continuous partial/final handling, Stop exclusion, final-drain ordering, exactly-once submission, reconnect behavior and microphone cleanup. No fresh Azure Speech request or physical microphone test was performed for this revision, so audio recognition accuracy remains unverified here.

### 2026-10-16 model-first interpretation revision

Recognized text now always enters the selected model's structured conversation interpreter before validated Python state changes. Speech transport, transcript finalization, duplicate suppression and SDK closure are unchanged. The browser suite passed, but no new physical microphone or Azure Speech request was performed.

### 2026-10-18 Neo4j MCP revision

The optional read-only Neo4j MCP integration does not change Speech transport, recognition, accounting, transcript ownership or provider routing. Its unit tests perform no Azure inference or Speech request. No new physical microphone or Azure Speech verification was performed for this revision.

### 2026-10-19 demo-day repair

The knowledge and research repairs do not change Speech transport, continuous recognition, transcript ownership or provider selection. The full browser unit suite passed; no physical microphone or new Azure Speech recognition request was performed.

### 2026-10-19 streaming document uploads

The upload changes do not alter Speech transport or recognition. Per explicit instruction, no automated or live Speech check was run.

### 2026-10-20 recovery integration

The MCP normalization, public-research, inventory-context and Groq structured-output recovery changes do not modify Speech transport, continuous recognition, transcript ownership, usage accounting or browser microphone behavior. No Speech test, live Azure Speech request or physical microphone check was run for this revision.

### 2026-10-21 automatic document-graph ingestion

The upload, Azure Document Intelligence and Neo4j reference-graph changes do not modify Speech transport, SDK lifecycle, transcript ownership, continuous recognition or usage accounting. No Speech test, live Azure Speech request or physical microphone check was run for this revision.

### 2026-10-21 observable investigation recovery

The planning, evidence verification, activity-stream and Investigation Activity changes do not modify Voice or Speech code, SDK lifecycle, continuous recognition, transcript ownership or usage accounting. No Speech test, live Azure Speech request or physical microphone check was run; the existing live Speech blockers remain unchanged.

### 2026-10-21 general intelligence experiment

The read-only time, calculation, weather, mapping, currency and holiday capabilities do not modify Voice or Speech code, SDK lifecycle, continuous recognition, transcript ownership or usage accounting. No Speech request or physical microphone check was performed; prior Voice validation and blockers remain unchanged.

### 2026-10-26 chat and URL-ingestion repair

The chat transcript and URL knowledge-ingestion changes do not modify Speech transport, SDK lifecycle, transcript ownership, continuous recognition or usage accounting. The Voice workspace remained accessible in a browser check; no microphone access, recognition session or live Azure Speech validation was performed.

### 2026-10-26 document-ingestion activation

The local persistence, URL ingestion and document graph activation does not modify Speech transport, SDK lifecycle, continuous recognition, transcript ownership or usage accounting. Voice readiness was reported by the running service; no microphone or live Azure Speech recognition request was performed.

### 2026-10-26 investigation validation recovery

Planner diagnostics, personal-memory lookup, evidence-reference recovery and failed-chat composer restoration do not modify Speech transport, SDK lifecycle, transcript ownership, continuous recognition or usage accounting. No microphone or live Azure Speech check was performed.
