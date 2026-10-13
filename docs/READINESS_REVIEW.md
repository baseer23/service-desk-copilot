# Browser readiness, UI and scope review — 2026-10-12

The implementation is saved. It remains a local demo pending Azure acceptance;
public production gates recorded in [VOICE_REVIEW.md](VOICE_REVIEW.md) still apply.
No commit, private `.env` edit/print, resource provisioning, live Azure request,
real microphone session or live Aura write was performed. Synthetic browser media
was used. Source snapshot before this phase:
`/private/tmp/deskmate-before-readiness-polish-20261012`.

## 1. Why the application works without Azure

The active development stack was launched with `./scripts/fake-voice-demo.sh` in
the preceding session. Its captured process session and status-only `/health`
response confirmed `TEST_MODE=true`, generation/embeddings `stub`, voice `fake`.
Azure endpoint/deployment configuration was absent. This launcher explicitly sets
all three providers, isolated SQLite/Chroma paths, loopback identity, disabled Aura
and a random temporary cookie secret. It does not edit `.env`. This phase restarted
the same launcher with the saved changes and fresh private temporary stores on
ports 8000/5173.

`stub` in historical timeline events is the deterministic reasoning provider's
internal source name. `providerLabel()` renders it as **Isolated demo** without
rewriting persisted data. Root and Voice Support banners say **ISOLATED DEMO —
Responses and audio are simulated. Azure is not being used.**

## 2. What the fake demo does

Use **Start text support** for deterministic contact confirmation, fixture/KB
retrieval through real local MCP, action outcomes and explicit case save. The
opening greeting is a TEST_MODE-only constant. Fake embeddings are deterministic
test vectors, not Azure vectors. Fake audio is zero-valued PCM: it does not recognize
speech or synthesize audible responses. **Test microphone** exercises permission,
capture, AudioWorklet, PCM framing and WebSocket delivery only. Clicking it manually
still requests actual browser permission; automated checks supply synthetic media.

## 3. Confirmed cause and reproduction

A synthetic isolated-browser reproduction found the original button enabled and
reachable. Its click created a session, opened the stream and produced a greeting
bubble/listening state; capture tracks stopped on Stop. No disabled-handler or
transparent click-blocking defect was established. The misleading expectation was
speech intelligence from a silent fake adapter, combined with weak styling and
limited visible progress. The old screen lacked a prominent demo explanation.

The final Playwright plumbing check proves button focus/style, `/pcm-worklet.js`
HTTP 200/JavaScript MIME, synthetic MediaStream/AudioContext/worklet setup, cookie
establishment, real proxied WebSocket, initial ping, session snapshot, listening,
outbound PCM frames and track cleanup. The implementation keeps capture before
HTTP session creation, so permission failure creates no orphan session. The HTTP
cookie is established before WebSocket connection. No hardware was accessed.

## 4. Fixes and action traces

| Confirmed issue | Scoped fix and regression |
| --- | --- |
| Fake speech looked like a working AI voice session | Demo banners, **Test microphone**, explicit no-recognition/no-speech explanation; unit and real-browser tests |
| Text support depended on Voice Live being enabled/configured | Persisted text/voice mode; text delivery transport receives Azure graph replies without a Voice Live connection; mock-Azure test with voice disabled |
| Normal mode used a canned opening | Azure structured `voice_greeting`; TEST_MODE alone retains the constant; generated-greeting regression |
| A fresh fake launch could leave the browser holding a cookie signed with the previous demo key | Only explicit new loopback demo creation replaces stale identity; resume/read retain owner validation; stale-cookie regression |
| Stop could leave a reconnect action pointing to a rejected terminal session | Hide reconnect after explicit Stop/completion; HTTP rejects terminal resumes with `VOICE_SESSION_TERMINAL`; frontend/backend regressions |
| Capture/processor/socket failures were hard to diagnose | Visible six-stage progression, useful fixed messages and expandable codes; denial/worklet/WebSocket tests |
| Sticky controls could cover connection status during visual review | Top sticky positioning below the bounded panels; browser geometry check and inspected screenshot |
| Delayed playback rejection could tear down a newer session | Check socket/player identity before handling the old promise; regression preserves the new listening session |
| Generating a live greeting introduced startup ordering/cancellation concerns | Wait for configured acknowledgement and greeting before listening/user workers; renew lease during preparation and cancel on Stop; rejection/order/cancellation tests |
| Real Voice Live config used an incompatible transcriber | `gpt-4o-transcribe` for `gpt-realtime`; real SDK serializer regression and [stable contract](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-api-reference-2026-04-10) |
| Normal health could construct live embedding/Aura dependencies | Application-only local-storage MCP probe and cached health status; forbidden-token/missing-Azure tests |
| Simulated readiness could make legacy Azure booleans misleading | Azure booleans require the Azure provider; fake UI labels reasoning/audio simulated; regression |
| SDK stdio logging escaped the status-only CLI | Explicit stderr sink passed to the SDK-owned subprocess context; real-process CLI regression |
| General questions could trigger unrelated research | Structured configurable scope verdict before investigation/tools; rejection/acceptance/model-authority tests |
| Ranking was fixed and fetched selection alone permitted ingestion | Configurable primary-source ranking/filtering including redirect checks; direct relevance required for ingestion; source/ingestion regressions |

Common dispatch remains React → authenticated FastAPI gateway → compiled voice
LangGraph → bound LangChain tools → lifespan-owned local stdio MCP. Generation
selection is StubProvider only with TEST_MODE or AzureOpenAIProvider in normal
mode. Embedding selection is the corresponding isolated/Azure provider behind MCP;
it runs when retrieval/ingestion needs vectors, not on an ordinary health request.

| Action | Frontend and backend trace | Providers, state, audio and visible result |
| --- | --- | --- |
| A. Start text support | `start(false)` → POST `/voice/sessions` `{mode:text}` → HttpOnly identity cookie → `connect(id)` → WS `/voice/sessions/{id}/stream` → initial `ping` | Fake runtime uses FakeVoiceLiveProvider + StubProvider/stub embeddings. Azure uses TextSupportTransport + AzureOpenAIProvider/Azure embeddings; the transport performs no model inference itself. Idle → connecting → configured/opening generated greeting → assistant_speaking → listening. Snapshot/transcript/status events show opening question. Microphone off; fake may emit silent output PCM, Azure text emits no audio. |
| B. Voice / Test microphone | `start(true)` → `VoiceAudio.start()` permission/capture/worklet → POST mode voice/cookie → same WS/ping | Fake runs FakeVoiceLiveProvider; Azure runs AzureVoiceLiveProvider with Azure reasoning/embeddings. Capture stays gated until the ready snapshot. Preparing → requesting permission → opening microphone → connecting → connected → listening; then user_speaking/processing/using_tools/assistant_speaking as applicable. Mic is on only after explicit permission. Fake input frames produce no speech recognition. Azure transcript/audio remains unvalidated live. |
| C. Reconnect with text | `start(false,true)` → POST `{resume_session_id,mode:text}` → atomic owner/status check and mode update → same WS route | Requires stopped/failed disconnected session, excluding explicit_stop/completed. Reloads turns/contacts/evidence/actions, resets sequences and creates a new connection. Azure reasoning/text transport; fake retains fake transport. No microphone, no replayed audio. Restored transcript and listening state; active/terminal conflicts have safe codes. |
| D. Stop session | `stop()` → WS `session.stop`, immediate local audio/socket cleanup, POST `/voice/sessions/{id}/stop` | Explicit stop recorded; workers/provider closed and lease released. Status stopped, tracks/context closed, composer disabled; new Start allowed and terminal reconnect hidden. Provider selection does not change. |
| E. Send typed message | `submit()` → WS `text.input` with current ID/increasing sequence → gateway validates and queues turn → compiled graph → approved reply delivery | Fake structured fixtures or actual Azure reasoning; embeddings only for tools that need them. processing/using_tools → assistant_speaking → awaiting_confirmation or listening; explicit outcome can complete. Stable final user/assistant bubbles, operational Activity and case controls update. In an active voice session the microphone continues unless muted/stopped; text mode has no mic. |

Insecure context, denied permission, unavailable device, unsupported 24 kHz capture,
processor load/resume, HTTP/session, socket/auth, provider configuration, timeout,
sequence and playback failures are bounded and surfaced with sanitized messages or
codes. Vite forwards `/voice` with WS and `/readiness` with HTTP. One origin must be
used consistently for cookies/Origin; credentials remain server-side. Unmount and
late-permission cleanup stop tracks, and abandoned HTTP creation is explicitly stopped.

## 5. Azure readiness now

The current local runtime reports isolated_demo, MCP/stores READY, text/test-mic
ready, and live Azure validated false. Azure CLI executable is MISSING; sign-in was
not inspected. No deployed model, role, reachable endpoint or authentication has
been validated. Provider initialization/configuration never means cloud connected.

`/readiness` reports per-capability configured, locally_initialized, authenticated,
reachable and live_validated independently. Normal HTTP does no token/cloud probe;
cloud fields stay NOT_CHECKED (fake: NOT_APPLICABLE) and live false. Store readiness
uses startup-local proof plus current MCP task state; it is not ongoing disk/Aura
monitoring. Missing Azure reasoning leaves diagnostics available, disables the
correct UI actions and returns an explicit generation 503 without fake fallback.

The status-only CLI starts/closes real local MCP and initializes additive structures
at configured private paths. Its real-process regression uses temporary stores and
asserts silent stderr/no paths/secrets. `--live` is a separate explicit opt-in for one
minimal reasoning request, one fictional embedding, and a silent Voice Live
configured acknowledgement/close. It was tested only with mocks and never executed
against Azure. CLI cloud results are point-in-time and do not alter HTTP readiness.

## 6–9. Decisions, settings, secrets and roles

The exact environment block, SDK endpoint contract and 18 operator steps are in
[AZURE_LIVE_SETUP.md](AZURE_LIVE_SETUP.md). Required non-secret names:

`TEST_MODE`, `DEPLOYMENT_MODE`, `GENERATION_PROVIDER`, `EMBED_PROVIDER`,
`AZURE_USE_ENTRA_ID`, `AZURE_FOUNDRY_ENDPOINT`, `AZURE_MODEL_DEPLOYMENT`,
`AZURE_OPENAI_API_VERSION`, `AZURE_STRUCTURED_OUTPUTS`, `AZURE_EMBEDDING_DEPLOYMENT`,
`EMBED_MODEL_NAME`, `AZURE_EMBEDDING_DIMENSIONS`, `MODEL_TIMEOUT_SEC`,
`VOICE_ENABLED`, `VOICE_PROVIDER`, `AZURE_VOICELIVE_ENDPOINT`,
`AZURE_VOICELIVE_MODEL`, `AZURE_VOICELIVE_API_VERSION`, `AZURE_VOICELIVE_VOICE`,
`VOICE_AUTH_MODE`, `VOICE_TENANT_ID`, `ALLOWED_ORIGINS`, `CASE_DB_PATH`,
`CHROMA_DIR`, empty `NEO4J_URI`. Scope/source defaults:
`KNOWLEDGE_SCOPE=support`, `RESEARCH_SOURCE_POLICY=prefer_primary`, optional
`RESEARCH_PREFERRED_DOMAINS` comma-separated domains.

`VOICE_SESSION_SIGNING_KEY` is always secret. Optional local-key authentication
requires secret `AZURE_OPENAI_API_KEY` and `AZURE_VOICELIVE_API_KEY`. Access tokens,
cookies, Authorization headers, connection strings and the private `.env` must not
be shared. Only region/type/deployment/model names, role booleans and safe error
codes are requested. Entra roles: Cognitive Services User + Foundry User for voice;
Cognitive Services OpenAI User for reasoning/embeddings. No role was assigned here.

## 10–12. UI, scrolling, scope and source quality

Filled/bordered primary, secondary, neutral, toggle, danger, success, send and subtle
buttons now share height/padding, hover/active/focus, disabled and busy states.
Responsive wrapping, microphone status text, timestamps, empty state, readiness
refresh and dedicated error alert are visible. Screenshots were inspected, including
the corrected controls below Activity. Focus and layout are tested geometrically,
not with exact pixels.

Timeline (1,000 events), Sources (20 huge pages), Voice transcript (1,000 messages)
and collapsible Activity have independent bounded scrolling. Evidence previews are
600 characters, full content renders only after **Show full evidence**, and state
keeps the full original content/URL/title/time/provenance. Keyboard focus can reach
scroll regions. Transcript keeps upward scroll and offers Jump to latest.

The existing structured classifier supplies `in_scope`; no production keyword
filter was added. Default support scope accepts VPN/MFA/printer, internal policy
and uploaded résumé/document questions; immigration news, sports, restaurants and
unrelated politics stop before MCP/web research. Broad mode requires explicit
configuration. Deterministic keyword examples exist only in test fixtures.

Primary domains are configurable and rank first; strict primary_only excludes
third-party URLs and final redirects. Third-party provenance remains visible.
Ingestion requires selected actual content and `directly_relevant=true`. Search
snippets cannot support diagnosis. A new Azure namespace is distinct from legacy
and isolated vectors; tests prove those old stores stay intact. Fresh acceptance
Chroma prevents previous unrelated knowledge from influencing the first demo.

## 13. Validation

Checks below use the final saved source. Logs:
`/private/tmp/deskmate-readiness-*.log`. No ordinary check accesses Azure, hardware
microphones or live Aura. Existing ownership, contact/case persistence, MCP write
authorization, real stdio, SDK serialization, duplicate/sequence and cancellation
tests remain part of the suite. Global dependency/lint debt was not mass-fixed.

| Check | Final result |
| --- | --- |
| `.venv/bin/python -m pytest backend/app/tests -q` | **174 passed, 2 skipped** (explicit Aura opt-in) |
| Targeted browser voice, SDK, Azure reasoning, MCP, embeddings, readiness, scope/source and web regression files | **99 passed** |
| `npm --prefix frontend test` | **27 passed**, three files |
| `npm --prefix frontend run build` | **TypeScript + Vite passed** |
| `npm --prefix frontend run test:e2e` | **12 passed**, isolated real HTTP/local MCP with synthetic media and explicit layout fixtures |
| Scoped Ruff / Black | **Passed**, new/refactored Python modules and regression tests (23 formatting-check files) |
| Scoped mypy `--follow-imports=skip` | **Passed**, 18 runtime source files |
| Scoped frontend ESLint / Prettier | **Passed**, voice/audio/readiness/evidence modules and tests |
| Real-process safe CLI | **Passed**, temporary stores; status-only stdout, silent stderr, no Azure |
| `.venv/bin/python -m pip check` | **No broken requirements** |
| `git diff --check` | **Passed** |
| Desktop and 390px manual browser inspection | **Passed**, bounded panels, focus ring, no horizontal page overflow |

Exact targeted command:

```bash
.venv/bin/python -m pytest backend/app/tests/test_browser_voice.py backend/app/tests/test_voice_sdk.py backend/app/tests/test_provider_azure_openai.py backend/app/tests/test_mcp.py backend/app/tests/test_embeddings.py backend/app/tests/test_azure_readiness.py backend/app/tests/test_scope_source_policy.py backend/app/tests/test_web_research.py -q
```

The CLI smoke explicitly supplied TEST_MODE/stub/fake, empty Azure/Aura values and
fresh temporary paths; it did not use private customer stores. Safe status probes
through the restarted Vite proxy report generation/embedding stub, voice fake,
Azure legacy configuration booleans false, and no live Azure validation. The
worklet asset returns HTTP 200 with `text/javascript`.

Screenshots: `frontend/test-results/voice-support-polished.png`,
`frontend/test-results/investigation-bounded-panels.png`, and
`/private/tmp/deskmate-readiness-mobile.png`. These contain fictional test data.
Global lint/dependency results in VOICE_REVIEW.md remain earlier repository debt;
they were not re-audited or broadly repaired. Legacy graph lint still has eight
out-of-scope findings; the current change adds no fixable import/unused-domain debt.

## 14. Files changed

This phase's source inventory is appended below. It is compared with the pre-phase
snapshot; the larger uncommitted Git tree also contains the preceding browser
migration. No unrelated reset, commit or broad format was performed. Formatting
of legacy main/graph/MCP files was limited to changed line ranges and imports.

### Phase file inventory (55 files)

```text
.env.example
backend/app/agent/graph.py
backend/app/agent/models.py
backend/app/agent/scope.py
backend/app/core/config.py
backend/app/diagnostics/__init__.py
backend/app/diagnostics/azure_readiness.py
backend/app/diagnostics/readiness.py
backend/app/main.py
backend/app/mcp/client.py
backend/app/mcp/server.py
backend/app/models/dto.py
backend/app/models/instructions.py
backend/app/models/provider_azure_openai.py
backend/app/services/capabilities.py
backend/app/services/source_policy.py
backend/app/testing/provider_stub.py
backend/app/tests/test_azure_readiness.py
backend/app/tests/test_browser_voice.py
backend/app/tests/test_provider_azure_openai.py
backend/app/tests/test_scope_source_policy.py
backend/app/tests/test_service_desk_scenarios.py
backend/app/tests/test_supervisor.py
backend/app/tests/test_voice_sdk.py
backend/app/tests/test_web_research.py
backend/app/voice/api.py
backend/app/voice/auth.py
backend/app/voice/azure_voicelive.py
backend/app/voice/graph.py
backend/app/voice/models.py
backend/app/voice/providers.py
backend/app/voice/service.py
docs/AZURE_LIVE_SETUP.md
docs/RAG.md
docs/READINESS_REVIEW.md
docs/README.md
docs/VOICE.md
docs/VOICE_REVIEW.md
frontend/README.md
frontend/playwright.config.ts
frontend/src/App.test.tsx
frontend/src/App.tsx
frontend/src/InvestigationPanels.test.tsx
frontend/src/InvestigationPanels.tsx
frontend/src/VoiceSupport.test.tsx
frontend/src/VoiceSupport.tsx
frontend/src/api.ts
frontend/src/readiness.ts
frontend/src/styles.css
frontend/src/voiceAudio.ts
frontend/src/voiceErrors.ts
frontend/tests/e2e.spec.ts
frontend/vite.config.ts
scripts/fake-voice-demo.sh
templates/tier-b-hosted-hybrid.env
```

## 15. Remaining blockers and emergency stop

Azure resource/model/region/quota choices, credentials/roles, CLI sign-in and explicit
live/microphone authorization are outstanding. Silent SDK conformance and fake
tests cannot establish live regional compatibility, speech fidelity, latency or
quota. A live test is required before claiming those work. Scope/relevance/source
judgments can misclassify; a configured primary-domain label does not verify an
article's facts. Source provenance and uncertainty remain visible.

Default first-live model timeout is 30 seconds. Initial provider open (25 seconds),
configured acknowledgement (10), and reasoning greeting (up to 61 including schema
correction) fit the 105-second UI preparation deadline. Higher operator timeout
overrides can exceed the UI deadline; use the documented default for acceptance.
An already dispatched synchronous SDK request can finish after cancellation but
remains bounded by its SDK timeout. Speech fidelity/semantic entailment, production
sign-in, non-voice API authorization, rate limiting, encrypted storage/deletion,
multi-replica operation and existing dependency debt remain earlier deployment gates.

Emergency: Stop session, close the tab or Ctrl+C. An authenticated same-origin
`POST /voice/sessions/{id}/stop` stops the owned session. Disable `VOICE_ENABLED`
and restart for prevention. Restore the source snapshot with voice disabled for
rollback; preserve `.env`, SQLite and Chroma. The fake launcher is stopped with
Ctrl+C. Current live setup and emergency instructions are in the setup guide.

## INFORMATION REQUIRED FROM ABDUL

1. Is a Voice Live-capable Microsoft Foundry resource already available?
2. Which Azure region is selected or planned?
3. Does that resource show `gpt-realtime` availability and usable quota?
4. Is an Azure OpenAI-compatible resource already available?
5. What is the non-secret reasoning deployment name?
6. Which structured-output model/version is deployed?
7. What is the non-secret embedding deployment name?
8. Is `text-embedding-3-small` deployed?
9. Choose Azure CLI/Entra ID (recommended) or keys entered privately in your editor.
10. Are Cognitive Services User, Foundry User and Cognitive Services OpenAI User assigned?
11. Use the same Foundry resource for both capabilities, or existing separate resources?
12. Use `http://127.0.0.1:5173` (recommended) or `http://localhost:5173` consistently?
13. Use completely new private temporary SQLite/Chroma directories (recommended)?
14. Re-ingest only fictional test knowledge for the first acceptance run (recommended)?
