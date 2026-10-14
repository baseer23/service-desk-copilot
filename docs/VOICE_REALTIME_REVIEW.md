# Voice conversation repair — 2026-10-14

The polished interface is preserved. This repair reduces reasoning and managed
state work on the audio path. It does **not** establish ChatGPT Voice-level latency
or complete live speech acceptance.

## Availability and authorization

The inspected persistent guard had CAD 0.336425 remaining under the approved
CAD 150 application allowance, including the CAD 90 infrastructure forecast.
Each configured two-minute Voice Live session reserves CAD 1. Voice unavailable
is the correct product status with that balance. Direct voice start now rejects
before creating an unusable conversation; text support remains independently
available when its paid request can be reserved.

The user explicitly declined the proposed CAD 155 allowance and confirmed
"Keep CAD $150; live acceptance remains blocked." No allowance increase,
historical-charge reset, refund of unknown usage, infrastructure-forecast reduction,
paid speech test or telephone call was performed during this repair. A cheaper
provider or a fake transcript is not substituted for Azure.

## Confirmed problems and changes

| Problem | Change and regression coverage |
| --- | --- |
| Every ordinary spoken clarification required extraction, planning and reply model calls in sequence. | One combined extraction/read-plan proposal can provide an informational question. Retrieval and a grounded action retain a second model request and the same MCP/evidence checks. Tests cover consecutive context, MCP grounding, contact confirmation and denied writes. |
| Full voice instructions appeared in both system and user messages; stages inherited a 1,200-token completion bound. | Include instructions once and bound greeting/proposal/reply output separately to 100/700/350 tokens. Evidence, actual context and schema constraints remain. |
| A live greeting and the first user turn could contend for the only paid-operation reservation. | Queue calls on the shared provider's async lock; the durable guard remains authoritative. An overlapping real SDK-shaped client regression verifies both calls complete without a concurrent reservation. |
| Transient VAD/activity writes and repeated managed state reads delayed transcription and subsequent audio events. | Deliver transient events directly; cache owner-scoped committed runtime state, coalesce final transcript persistence with response completion, and still validate every persistent mutation. Slow-write and cached-copy regressions verify boundaries. |
| PCM append waited behind managed policy refreshes. | The independently refreshed policy check can serve audio for at most one second. Stale or completed failed checks block audio; watchdog cadence, full reservation, duration and emergency safeguards remain. |
| Interruption waited for transcript persistence and left already queued audio to drain. | Clear playback/drop queued audio promptly; checkpoint interrupted turns on the next mutation or teardown. Completed previous turns remain final. |
| Late ASR and queued older turns could use a newer interruption epoch; an interrupted graph continued subsequent paid stages. | Correlate VAD item IDs to epochs, skip stale queued turns, and abandon further stages/tools after an in-flight request returns. Already dispatched paid requests are not retried or presumed cancelled. |
| An SDK response cancelled during its policy check left a correlation slot although nothing was dispatched. | Return explicit dispatch status and remove only the undispatched slot; the next response is retained. |
| Muting mid-sentence stopped all frames and could leave VAD waiting. | Disable the microphone track and send zero PCM frames; never send captured samples while muted. Real browser capture and unit/UI checks verify unmute and track shutdown. |
| An action could be phrased as a clarification to avoid evidence requirements. | Reject action prompts disguised as questions through the common reply validator. Contact confirmation, canonical evidence, write authorization and deterministic resolution remain application-owned. |
| The last permitted answer could remain partial when the session ended. | Finalize its durable transcript and drain bounded queued output before stopping. |

Server VAD remains threshold 0.4, prefix padding 400 ms, silence 500 ms,
`create_response=false`, `interrupt_response=false`, `auto_truncate=true`.
PCM remains mono PCM16LE at 24 kHz in 20 ms frames. These previously accepted
Azure fields are preserved pending real comparative testing. The installed SDK
1.3.0 rejects `speech_duration_ms` as a `ServerVad` constructor argument; no
unsupported field was added. The model remains `gpt-realtime` with
`gpt-4o-transcribe`, not an incompatible Azure Speech transcription selection.
See the [Microsoft stable Voice Live reference](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-api-reference-2026-04-10).

## Validation and remaining gates

The physical browser check uses Google Chrome with authorized
microphone permission and no fake media. It verifies capture, 24 kHz context,
silent mute, unmute and stopped tracks without recording raw audio or starting
a paid speech session.

| Check | Result |
| --- | --- |
| Complete backend suite, including ingestion, PDF, managed integration contracts and MCP | 309 passed, 2 skipped |
| Documented targeted voice/MCP/retrieval checks plus conversation regressions | 147 passed |
| Frontend unit/UI tests | 49 passed |
| TypeScript and Vite production build | Passed |
| Isolated HTTP Playwright suite | 21 passed |
| Changed voice implementation and new regression file Ruff check | Passed |
| Dependency consistency and `git diff --check` | Passed |
| Authorized physical Chrome capture | 174 frames, 118 non-silent, 24 kHz context; mute sends only silence, unmute resumes, track ends; no browser errors |
| Restarted actual local interface | All four navigation buttons work; text enabled, voice correctly unavailable; no alerts/browser errors |
| Direct unfunded voice start on real restarted backend | HTTP 503 / AZURE_VOICE_NOT_READY before a session is created |
| New real Azure ASR/TTS/latency and physical speech conversation acceptance | Blocked by the explicitly retained CAD 150 allowance |

Detailed private logs and capture counters are under the existing private repair
directory. They contain no persisted microphone audio. Isolated provider tests and
SDK contract tests are not counted as live Azure speech acceptance.

Previous real Azure evidence in DEMO_READINESS used two prerecorded fictional
utterances, recognized both and returned spoken responses, but observed roughly
15–30 seconds of response delay. That evidence belongs to the earlier code.
It does not validate this repair, human microphone recognition, current hosted
managed-identity speech, or transcription arriving before speech ends.

Required live acceptance after sufficient explicit funding is available:

1. Read protected `/admin/readiness`; confirm the configured reservation fits,
   the guard is enabled, one session is allowed and the original charges remain.
2. Open `http://127.0.0.1:5173` in an authorized browser, select the physical
   microphone, use headphones, and start voice once.
3. Speak at least three fictional turns without pressing between-turn buttons.
   Record first partial/final transcription, speech end, graph completion, first
   audio and playback start times. Measure each turn and interruptions separately.
4. Interrupt during audible playback. Verify immediate local clearing, no old
   audio resuming and a response to the new utterance. Repeat during reasoning.
5. Mute mid-utterance, unmute, verify continued listening, switch devices between
   sessions, and explicitly stop. Confirm hardware tracks and Azure socket close.
6. Check retained context, no repeated greeting/question, grounded MCP actions,
   and consistent persisted final/interrupted turns. Repeat on the deployed host
   with its managed identity and authorized Entra browser session.

Do not mark live acceptance complete until actual recognized speech and spoken
responses on this revision, sustained turn-taking and measured latency are verified.
No measured improvement is claimed from unit-test timing. Existing spending,
concurrency and duration limits can end a session and remain intentional.
