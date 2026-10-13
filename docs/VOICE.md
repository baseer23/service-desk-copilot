# Browser Voice Support

For the latest repair results and the still-blocked Azure activation, see
[REPAIR_REVIEW.md](REPAIR_REVIEW.md). Local PDF/text extraction, document saving,
case saving and cited keyword retrieval work independently of Azure reasoning.
Semantic indexing and generated answers retain the cost guard and acceptance
policy; local documents do not create substitute vectors.

DeskMate uses React microphone/output ↔ authenticated FastAPI WebSocket ↔ Azure Voice
Live's official async SDK. A separate compiled LangGraph proposes extraction,
validates application confirmations, plans read capabilities through discovered MCP
schemas, gathers evidence and returns one grounded action or question. Azure OpenAI
supplies structured reasoning, including the live greeting; a canned greeting exists
only in TEST_MODE. Azure text support delivers the same graph-approved responses
without opening Voice Live. Voice Live transcribes input and speaks the approved
reply. This uses Voice Live model mode, not a hosted Foundry Agent.

## Boundaries and security

Azure credentials stay on the server as SecretStr/DefaultAzureCredential. The UI
receives a signed HttpOnly SameSite=Strict identity cookie and only its own session.
Local-demo identity is restricted to loopback hosts/clients. Every mutation and
WebSocket checks Origin; cookie signatures and owner/tenant checks apply to HTTP,
SQLite and voice MCP tools. `DEPLOYMENT_MODE=production` rejects test providers,
API-key mode and local-demo identity. Deployed authentication exchanges a validated
Entra v2 bearer token for that cookie; a host sign-in integration must supply the
Authorization header on initial POST (the standalone demo UI does not implement
MSAL sign-in). A random process-only cookie signing key is generated when absent;
configure a stable secret to preserve owner access across restarts.

The model cannot confirm profile fields or resolution or execute writes. Proposed
contact quotes must occur in the current user turn and are parsed independently.
Email/callback/name have explicit confirmation and a bounded correction budget.
A material action needs canonical evidence IDs, cannot repeat a previous action,
and must pass a conservative elevated-action filter. These controls do not prove
semantic entailment of every model-selected recommendation; live evaluation is
still required. Public evidence is untrusted. Both voice and existing case Activity show fixed operational
labels; model draft justification is not rendered. Owner-visible transcripts intentionally contain the user's
contact details; no contact details or raw SDK errors go into operational events.

Audio uses documented **mono PCM16LE at 24 kHz**. The browser sends 20 ms frames,
not MediaRecorder containers. Azure server VAD has `create_response=false` so
transcription cannot trigger unverified answers. The backend cancels responses on
barge-in; the browser clears queued playback. Voice Live's speech renderer is
instructed to read the approved reply faithfully. The gateway streams transcript deltas only while they match the approved reply
and rejects divergent or missing final renderer transcripts. The UI keeps a stable
bubble. Audio can arrive before divergence is detected, so live spoken fidelity
remains an acceptance risk.

SDK: `azure-ai-voicelive[aiohttp]==1.3.0`; API explicitly pinned to `2026-04-10`.
The SDK creates the `/voice-live/realtime` transport from the HTTPS resource origin.
For `gpt-realtime`, input transcription uses the supported `gpt-4o-transcribe`;
`azure-speech` is incompatible with this multimodal selection.
References: [Voice Live guide](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-how-to),
[stable event API](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-api-reference-2026-04-10),
[Python SDK](https://learn.microsoft.com/en-us/python/api/overview/azure/ai-voicelive-readme?view=azure-python).

MCP stays **stdio**, owned by FastAPI's lifespan. The cloud speech model never
connects directly to local stdio. The application LangGraph performs backend
function dispatch through bound LangChain/MCP tools. This model-mode choice needs
no public remote MCP service or Foundry project/agent. A future cloud Agent direct
MCP design would require an authenticated Streamable HTTP service; none is exposed
here. Microsoft documents the cloud Agent remote-only transport requirement in
[Foundry MCP authentication](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/mcp-authentication?view=foundry).
Audio frames are never MCP calls. Unexpected native SDK function calls get a
bounded rejection; only the graph advertises permitted read schemas. Session
inspection/summary/evidence and `create_case_from_voice_session` require an owner
identity and are excluded from model plans. Save case is an explicit UI action.

## State and transport

Statuses: idle, connecting, listening, user_speaking, processing, using_tools,
assistant_speaking, awaiting_confirmation, completed, failed, stopped.

| Surface | Operation |
| --- | --- |
| POST `/voice/sessions` | Start, or owner resume with `{resume_session_id}` |
| GET `/voice/sessions/{id}` | Read owned conversation |
| POST `/voice/sessions/{id}/stop` | Durable stop and close the provider |
| POST `/voice/sessions/{id}/create-case` | Explicit MCP case save |
| WS `/voice/sessions/{id}/stream` | Duplex typed audio/text/events |

Version-1 envelopes carry type/session_id/sequence/payload. Client sequences start
at 1 per connection: duplicates are ignored, gaps/malformed messages end the
connection. Reconnect reloads persisted turns and confirmation/evidence/action
state and resets sequences; a text reconnect needs only Azure reasoning and uses
text delivery without a Voice Live connection. It never replays audio.
Reconnect with text switches the persisted session to text mode. Explicit stop/completion
cannot reconnect; start a new session instead. One actor has one active session; a
SQLite connection lease prevents two clients claiming the same conversation.
The lease renews every 5 seconds and expires after 20. Atomic writes use BEGIN
IMMEDIATE with rollback and lease verification. Case insertion and session linking
share a transaction and deterministic ID.

Limits: 64 KiB browser messages, 9,600-byte maximum input PCM frame, input queue 16,
turn queue 4, output queue 64, 2-second queue wait, SDK handshake 15 seconds/backend
connect 25, configured acknowledgement 10, SDK close 5, send 5, MCP turn tool call 20,
default session 900 seconds,
1,000 transcript turns, 4 read calls/turn and 40 retained canonical evidence items.
Preparation waits for the configured acknowledgement and generated live greeting
before listening/user processing, renewing the lease and honoring Stop throughout.
The UI preparation deadline is 105 seconds for the documented 30-second model timeout.
Browser send buffer cap 256 KiB, pending audio queue 64 chunks, playback cap 128
sources/15 seconds. Saturation
fails explicitly and closes both directions. No automatic retry of ambiguous audio
operations. Reasoning has a 30-second SDK deadline and one schema correction.

SQLite tables: voice_sessions, voice_transcript_turns, voice_session_events. Audio
is never stored. Raw transcripts/candidate quotes expire after configured retention
on the next new-session maintenance pass. Confirmed contact details, issue summaries,
evidence and saved cases remain; establish a tenant deletion/retention policy before
production. Expiry is lazy, not a guaranteed scheduled deletion service.

## Azure resources and exact configuration

Provision in one region that supports the selected model and has quota:

1. Microsoft Foundry resource with Voice Live enabled/available and `gpt-realtime`
   support. Copy its HTTPS **resource** endpoint, e.g.
   `https://RESOURCE.services.ai.azure.com`. Use the standard voice `en-US-AvaNeural`.
   This built-in Voice Live model name is not your text-model deployment name.
2. Azure OpenAI-capable resource (may be the same Foundry resource) with a structured
   chat model deployment, e.g. **gpt-4.1** named `deskmate-reasoning`, and
   **text-embedding-3-small** named `deskmate-embedding`. Copy its resource's
   Azure OpenAI endpoint, e.g. `https://RESOURCE.openai.azure.com`.
3. For hosting: HTTPS/WSS application host with WebSocket proxy support, persistent
   disk for local SQLite/Chroma, managed identity, and secret storage for the cookie
   key. Use one application replica for this local SQLite demo. Optional Aura is
   independent. No Azure AI Search or separate speech resource is needed here.
4. Managed identity/local Entra principal roles: **Cognitive Services User** and
   **Foundry User** on Voice Live; **Cognitive Services OpenAI User** on text and
   embedding resource. See [Voice Live authorization](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-how-to)
   and [Azure embedding identity/dimensions](https://learn.microsoft.com/en-us/azure/search/cognitive-search-skill-azure-openai-embedding).
   Deployment authentication also requires your tenant's Entra API registration
   (v2 access tokens with the API audience) and a browser sign-in integration.

For the first loopback live test, set exactly:

```dotenv
TEST_MODE=false
DEPLOYMENT_MODE=local
GENERATION_PROVIDER=azure_openai
EMBED_PROVIDER=azure_openai
AZURE_USE_ENTRA_ID=true
AZURE_FOUNDRY_ENDPOINT=https://RESOURCE.openai.azure.com
AZURE_MODEL_DEPLOYMENT=deskmate-reasoning
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_EMBEDDING_DEPLOYMENT=deskmate-embedding
EMBED_MODEL_NAME=text-embedding-3-small
AZURE_EMBEDDING_DIMENSIONS=384
VOICE_ENABLED=true
VOICE_PROVIDER=azure_voicelive
AZURE_VOICELIVE_ENDPOINT=https://RESOURCE.services.ai.azure.com
AZURE_VOICELIVE_MODEL=gpt-realtime
AZURE_VOICELIVE_API_VERSION=2026-04-10
AZURE_VOICELIVE_VOICE=en-US-AvaNeural
VOICE_AUTH_MODE=local_demo
VOICE_TENANT_ID=live-acceptance
VOICE_SESSION_SIGNING_KEY=<random-secret-at-least-32-characters>
ALLOWED_ORIGINS=http://127.0.0.1:5173
NEO4J_URI=
CASE_DB_PATH=<new-private-temporary-directory>/cases.db
CHROMA_DIR=<same-directory>/chroma
```

Run `az login` as the authorized principal locally; production uses managed identity.
Explicit local key option: `AZURE_USE_ENTRA_ID=false`,
`AZURE_OPENAI_API_KEY=<resource key>` and
`AZURE_VOICELIVE_API_KEY=<voice resource key>`. No VITE credentials and no silent
fallback. `AZURE_FOUNDRY_PROJECT_ENDPOINT` is not used in selected model mode.

## Local isolated fake demo

Run `./scripts/fake-voice-demo.sh`, then open http://127.0.0.1:5173. It creates private
new SQLite/Chroma paths and overrides runtime provider settings without touching
`.env`. Choose **Start text support** (no microphone). Try: `My VPN is broken`,
`My name is Alex Test`, `yes`, `Email is alex at example dot com`, `yes`,
`Callback: +12025550123`, `yes`. Ingest the fictional VPN KB below first to see
an evidence-backed action. Reply `it worked` or `it did not work`, then save the
case. The fake emits silent PCM for plumbing; it does not synthesize or recognize
real speech. **Test microphone** explicitly requests permission to exercise capture/transport
only. It never recognizes speech or speaks. The UI shows **ISOLATED DEMO** and
replaces operator-facing `stub` labels with **Isolated demo**; use text to avoid hardware access. Ctrl+C stops the demo.

Fictional KB: “If VPN is broken, check whether the VPN client shows connected.”

## One explicit live Azure acceptance

1. Verify region/model quota, roles and resource endpoints; set budget alerts.
   Keep `VOICE_ENABLED=false` until the host is configured. Do not reuse customer
   data. Create a new private temporary directory and set the live variables above.
2. Use one authorized tester, localhost/HTTPS, headphones and a browser with
   AudioWorklet. Keep the prior application/data backup. Start `make dev`.
3. Check `/health`: azure_openai for generation/embeddings, azure_voicelive for
   speech, test_mode=false, MCP/storage connected, configuration booleans true.
   Health does not prove Azure reachability.
4. Ingest the fictional KB through normal ingestion. This separately verifies Azure
   embeddings. Run a simple document question to verify structured Azure reasoning.
5. Click **Start voice chat** once. Grant permission. Verify greeting, microphone
   indicator and listening state. Speak the fictional VPN issue. Verify one stable
   transcript bubble per turn, audible reply and one useful next question.
6. Give a fictional name, spoken email and an authorized test contact number;
   correct one value, then confirm each. A callback is stored only; it is never
   dialed. Verify country clarification for a number without a country code.
7. Verify MCP evidence/activity and one safe action. Interrupt midway: audio must
   stop promptly and the new user turn must take over. Test mute/unmute and typed
   correction in the same session.
8. Say the action failed: verify no resolution claim/repetition. Test insufficient
   evidence and escalation. In a fresh session, explicitly say `it worked` after
   an action; verify user_confirmed_resolved, final summary and explicit case save.
9. Stop/disconnect and check browser microphone indicator off, provider closed,
   terminal session and sanitized operational events. Reconnect a disconnected
   session through text and verify state restoration. Repeat permission rejection.
10. Record actual SDK errors by category, latency, spoken/text fidelity and Azure
    usage. Do not log credentials/transcripts in diagnostic telemetry. Disable voice
    again and remove acceptance data according to your retention policy.

## Emergency stop and rollback

Click **Stop session** or POST its authenticated `/stop` endpoint from the same
browser origin. Close the tab to release microphone tracks and both WebSockets.
If the UI is unavailable, stop `make dev`/the hosting process; session provider
connections close on shutdown or timeout. Set `VOICE_ENABLED=false` and restart to
prevent new sessions. No telephone hang-up is needed because this feature has no
telephone connection. For rollback, restore the saved source snapshot and deploy
configuration with voice disabled; preserve `.env`, SQLite and Chroma. Browser
session tables are additive; old operational tables/collections are left intact.
Restore a database backup only if intentional data rollback is required.

Migration: the previous outbound ACS/Call Automation experiment, callback routes,
DTMF handling and operator controls have been removed. Those terms occur only in
this migration note. United Cloud/PSTN integration is outside this phase. Old
provider/embedding selections must be migrated to Azure; unsupported live provider
values are rejected. Old unknown environment keys are ignored, not used. Existing
local embedding collections are not compatible with Azure vectors: re-ingest source
documents into a new verified Azure collection; never reset the previous collection.

## Checks and deployment limits

```bash
.venv/bin/python -m pip check
.venv/bin/python -m pytest backend/app/tests -q
.venv/bin/python -m pytest backend/app/tests/test_browser_voice.py backend/app/tests/test_voice_sdk.py backend/app/tests/test_provider_azure_openai.py backend/app/tests/test_mcp.py backend/app/tests/test_embeddings.py backend/app/tests/test_azure_readiness.py backend/app/tests/test_scope_source_policy.py backend/app/tests/test_web_research.py -q
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run test:e2e
npm --prefix frontend audit
```

No ordinary test contacts Azure, uses a real microphone, or writes live Aura data.
SDK conformance tests use actual request serializers and typed server events;
passing fake tests alone does not establish the live adapter works with provisioned
Azure resources. Browser tests use real isolated HTTP/MCP plus synthetic media.
The 1,000-message browser test verifies fixed transcript/layout height and Jump to
latest. Scoped formatting/lint/type checks are recorded in VOICE_REVIEW.md.

This is a local demo, not ready for public production. Remaining gates include live
Azure speech fidelity/latency, sign-in integration for the deployed UI, tenant-wide
case API authorization (existing text APIs are not owner-scoped), abuse/rate limits,
encrypted storage and complete deletion policy, multi-replica database/session design,
and existing dependency/lint debt. Each turn can use scope classification, extraction, planning and reply
Azure calls plus embedding/tool requests and Voice Live speech; measure latency and
cost. Input/history is bounded, no audio persists, but cancellation can leave an
already dispatched synchronous SDK reasoning request running until its SDK deadline.

## Readiness, scope and interface updates

Use [AZURE_LIVE_SETUP.md](AZURE_LIVE_SETUP.md) for the precise 18-step portal/local
checklist, secrets guidance and exact settings. `AZURE_FOUNDRY_ENDPOINT` is passed
as the Azure OpenAI SDK resource origin (`.openai.azure.com`), never a project URL.
Start live acceptance with voice disabled, fresh private SQLite/Chroma paths, and
fictional knowledge; verify text reasoning before ingestion/embeddings, then voice
last after authorization. No old Chroma data is deleted.

GET `/readiness` separates configured, locally initialized, authenticated, reachable
and live validated. `/health` uses cached local MCP/store proof without cloud/token
calls. Missing reasoning keeps diagnostics available but returns an explicit 503
on generation routes; it cannot select a deterministic fallback. Text support needs
reasoning/local identity/stores; voice additionally needs embeddings and Voice Live.
The safe CLI is `.venv/bin/python -m backend.app.diagnostics.azure_readiness`; it
initializes additive local structures and prints statuses only. Only `--live`
authorizes a minimal reasoning request, fictional embedding and silent Voice Live
configuration handshake. It was not run against Azure in this task.

The UI shows permission/audio/connection progress, safe expandable error codes,
filled/bordered accessible buttons and independent transcript/activity/timeline/
source scrolling. Evidence previews are bounded; **Show full evidence** retains
access to original data. Reconnect is offered for unexpected disconnection/failure,
not after explicit Stop or completion.

`KNOWLEDGE_SCOPE=support` uses structured intent classification for IT, internal
knowledge and uploaded-document requests. Unrelated general requests stop before
research; `broad` explicitly opts into general knowledge. Source policy prefers
configured primary domains, labels third-party content, can enforce `primary_only`,
and requires an actual-content `directly_relevant=true` verdict before ingestion.
Search snippets never become diagnostic evidence. Scope/source judgments remain
model judgments, not a complete security boundary.

Current runtime traces, regressions and results are in
[READINESS_REVIEW.md](READINESS_REVIEW.md). The earlier migration review remains in
[VOICE_REVIEW.md](VOICE_REVIEW.md).


## Local Azure cost controls

Azure requests are disabled by default. Explicit `AZURE_USAGE_GUARD_ENABLED=true`
is required; `AZURE_EMERGENCY_DISABLE=true` blocks new operations. The authenticated
loopback **Disable Azure usage** control persists a disable flag in a separate
owner-only SQLite ledger and closes live sessions. A restart cannot clear that flag.
Use `scripts/azure-emergency-stop.sh <private-server-pid-file>` to disable usage and
stop only the verified DeskMate development processes. Keep the ledger for review.

Defaults are an estimated CAD 8 monthly application allowance, one live support
session at a time, at most two connection starts per UTC day, 10 minutes per session,
and 20 user turns. Every API/MCP process shares the same ledger. Reservations are
atomic and precede dispatch; complete reported token counters finalize text/embedding
estimates. Failed or unreported operations retain their reservations. Voice reserves
its whole configured duration; actual elapsed duration and reported audio/text token
counters are stored without transcripts. Outstanding SDK threads can finish after a
caller cancels; their usage is still recorded. A crash leaves active reservations
blocked until an operator reviews recovery.

These are **estimates, not an Azure invoice cap**. Conservative CAD assumptions are
10 per million input tokens, 40 per million output tokens, 1 per million embedding
tokens, and 0.50 per voice minute. They are not published account prices or an FX
conversion. Other subscription resources, taxes, delayed billing, hidden/unreported
usage and inaccurate estimates remain outside the guarantee. A subscription-wide
CAD 10 Cost Management budget sends alerts and cannot stop Azure charges.

Authorized setup uses `AZURE_SETUP_VALIDATION_MODE=true` with a separate CAD 2
estimate ceiling shared by all processes. Synthetic setup voice connections are
bounded to at most 60 seconds and reserve one estimated minute. Setup selects manual
commit in its **first** session update; Azure rejects changing VAD type later.
Silence never establishes speech recognition. Normal microphone sessions retain
server VAD. After setup, return setup mode to false. The first completed or closed
interactive voice acceptance session automatically persists emergency disable.

`AZURE_LIVE_TEST_MODE=true` restricts knowledge to one small pasted document, pins
one authenticated local tester, blocks PDF/URL ingestion and public web capabilities,
and enforces bounded input/output. The app cannot verify that operator-supplied text
is fictional; use the documented fixture only. Minimum deployment capacity is a
throughput allocation, not a billing limit. Pace calls using the verified deployment
rate limits; pacing cannot make an oversized request fit its TPM allocation.

Readiness displays remaining estimated allowance, sanitized live-check failures and
whether voice was transport-validated. Configuration and handshake success do not
prove microphone recognition or full workflow readiness. The actual activation
results and unresolved gates are in [AZURE_ACTIVATION_REVIEW.md](AZURE_ACTIVATION_REVIEW.md).
