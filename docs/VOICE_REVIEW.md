# Browser voice implementation review — 2026-10-12

This records the earlier browser migration checks. The subsequent readiness/UI/scope
review, updated test totals and exact runtime traces are in
[READINESS_REVIEW.md](READINESS_REVIEW.md); current Azure portal instructions are in
[AZURE_LIVE_SETUP.md](AZURE_LIVE_SETUP.md).

The requested browser implementation is saved and passes isolated checks. It is a
local demo. **Public production deployment is blocked** by live acceptance, deployed
sign-in integration, existing authorization/dependency debt and storage operations.
No live Azure session, real microphone access, telephone call or commit was made.
`.env`, customer SQLite/Chroma files and live Aura data were not modified. Source
backup: `/private/tmp/deskmate-before-browser-voice-20261012`.

## 1. Removed outbound implementation

Migration inventory: deleted the uncommitted `voice/azure_acs.py`, `events.py` and
`state_machine.py`, the phone-only `VoiceCalls.tsx`/test, and the old
`test_voice_api.py`, `test_voice_mcp.py`, `test_voice_normalization.py`,
`test_voice_state_machine.py`, `test_voice_webhook.py`. Removed obsolete SDK tests
from the retained Azure reasoning test file. Replaced old voice models, repository,
service, API and provider protocol; removed operator tools/settings/UI, callback
routes and phone-form controls. No real dialing capability remains. Existing old
operational tables are left untouched, with no runtime readers/writers.

Occurrences of old telephone terms in this section and VOICE.md's migration note
are intentional history, including the deleted adapter filename. Runtime references
in the user's clean-removal list are absent. United Cloud/PSTN is outside this demo.

## 2. Generic code retained and adapted; confirmed fixes

Retained Azure structured reasoning, canonical case schemas, LangGraph, bound read
MCP tools, lifespan-owned stdio, SQLite case memory, Chroma, optional Aura, ingestion,
SHA-256 deduplication and existing text investigations. Moved deterministic providers
to `backend/app/testing`; normal runtime cannot select them. Shared versioned case
instructions replace provider-specific prompts. Live embeddings now use Azure with
resource/deployment/model/dimension-specific collection names; old collections remain.

| Confirmed implementation problem | Fix / regression evidence |
| --- | --- |
| Feature performed telephone intake instead of browser support | Browser session gateway/UI; real HTTP/MCP Playwright flow |
| Fake accepted a user-message dict rejected by the real SDK | Typed UserMessageItem/InputTextContentPart; actual SDK serializers exercised without network |
| Disconnect could cancel cleanup before durable terminal state | Shield teardown, close both transports, stop tracks; disconnect/reload tests |
| Barge-in before response.created could allow late audio | Track pending response epochs, reject cancelled response IDs, clear playback; late-created/audio regression |
| Old-session reconnect could coexist with a newer session | Atomic per-owner exclusion and connection leases; competing-session regression |
| Unconnected abandoned session could block future starts | Expire abandoned state/lease on new-session maintenance |
| Saved voice case could be accessed through unguarded case/request routes | Owner gate for case/read/events/resume, filtered list; owner and wrong-owner HTTP tests |
| Model-proposed contact could differ from the user's words | Quote must occur in current turn; deterministic parsing of quote, separate candidates/confirmations; forged/correction tests |
| Repeated uncertain confirmations could loop | Retry budget/manual typed correction path; bounded confirmation regression |
| Bare yes to a clarification could falsely imply resolution | Bare yes/no needs the action-result prompt; explicit success otherwise required |
| Action/write selection could escape the intended boundary | Discovered-schema/read allowlist validation, evidence IDs, elevated-action filter; real MCP authorization/write rejection tests |
| Duplicate/out-of-order events could produce repeated turns | Per-connection sequence rules, bounded event deduplication and persisted user turn IDs |
| Client pending audio promises lacked a queue bound | Limit 64 pending chunks, 256 KiB send buffer and bounded playback; saturation/ordered-audio tests |
| Speech renderer could disagree with approved text | Verify streaming prefix/final transcript; cancel divergent/no-transcript responses; fidelity regression |
| UI activity showed arbitrary model event/verification drafts | Fixed operational labels in voice and existing case UI; private-draft regression |
| Saving before resolution then saving again left stale outcome | Explicit resave updates owned case voice metadata/final summary atomically, without duplicates or overwriting an active investigation |
| Azure client/identity resources lacked explicit shutdown | Lifespan closes owned reasoning client/credential; closure regression |

Contact and transcript PII are intentionally visible only to their session owner.
Operational events contain fixed status labels; no raw audio, Azure key or SDK error
body is persisted there. SecretStr covers API/cookie/Aura secrets. SDK voice diagnostics
are suppressed so audio/transcripts/raw errors are not copied into application logs.

## 3. Previous provider reference removal

Removed the obsolete generation adapter, its generation tests and all associated
settings, default/model values, environment examples, UI messages, fallback paths,
setup instructions and deployment-template references. Historical validation documents
retain neutral descriptions of their original results and explicitly predate Azure
acceptance. They are not represented as Azure validation. Existing private `.env`
was preserved: unknown obsolete keys are ignored; unsupported provider selections
fail validation. Current working-tree source/documentation scan has zero matches for
the prohibited provider/model names, excluding protected `.env` and deleted/history
objects. A regression scans current source, docs, examples, scripts and templates.

## 4. Final Azure-only architecture

React AudioWorklet (24 kHz mono PCM16) ↔ owner-authenticated FastAPI WebSocket ↔
official async Azure Voice Live model-mode session. Azure transcribes and speaks.
A compiled bounded support LangGraph calls Azure OpenAI for extraction/read planning/
grounded reply, invokes discovered bound LangChain tools through the **real stdio
MCP client/server**, and waits for the next user turn. Deterministic code owns contact
confirmation, action outcomes and user-confirmed resolution. Explicit case save is
an owner-authorized MCP write. Audio stays outside MCP and persistent storage.

Model mode with application function dispatch is chosen deliberately. The cloud
Agent Service accepts remote MCP endpoints, not local stdio; we expose no remote
MCP server. See [Microsoft's transport/auth guidance](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/mcp-authentication?view=foundry).
The SDK supports native tool events, but no tools are advertised to the speech
renderer. Unexpected calls receive a rejection; the graph owns actual tool dispatch.

## 5. Files changed

The full working-tree inventory is appended below. It includes pre-existing
uncommitted work retained and adapted. The deleted telephone files in section 1 were
untracked before this migration, so Git's deleted-file list alone cannot show them.

## 6. Exact checks and results

| Check | Result |
| --- | --- |
| `.venv/bin/python -m pytest backend/app/tests -q` | **140 passed, 2 skipped** (explicit Aura opt-in) |
| Targeted browser voice + SDK + Azure reasoning + MCP + embeddings pytest files | **47 passed** |
| Real stdio MCP | Included in targeted/backend checks: four existing MCP tests, voice retrieval and owner-authorized voice tools |
| `npm --prefix frontend test` | **19 passed** (12 existing app, 7 voice/audio/scroll tests) |
| `npm --prefix frontend run build` | TypeScript + Vite **passed** |
| `npm --prefix frontend run test:e2e` | **8 passed**, real isolated HTTP/MCP; includes 1,000-message scroll test |
| Scoped Ruff / Black / mypy / frontend ESLint | **Passed** for new/refactored voice/provider modules and regression tests |
| `.venv/bin/python -m pip check` | **No broken requirements** |
| `git diff --check` | **Passed** |
| Working-tree removed-provider / telephone-runtime scans | **Passed**; intentional migration-only telephone terms retained |
| `npm --prefix frontend audit --json` | **31 advisories**: 2 critical, 24 high, 4 moderate, 1 low |
| Temporary pip-audit scanner over installed `.venv` | **113 advisories in 19 packages**, including dev/obsolete installed packages |

Scoped Python runtime mypy command uses `--follow-imports=skip` to distinguish new
modules from existing repository typing debt. No remote MCP tests apply to the
selected model-mode architecture. SDK tests use installed serialization helpers and
service event models, not just the fake adapter. Ordinary checks do not access
Azure, real microphones or live Aura.

Logs/reports are preserved at `/private/tmp/deskmate-browser-*.log` and the audit JSON
files there. Audit scanning tools were installed only under temporary storage.
No broad dependency upgrade or unrelated automatic formatting was performed.

## 7. Required Azure resources and environment

Provision a Voice Live-capable Foundry resource with regional `gpt-realtime` support;
an Azure OpenAI endpoint with `deskmate-reasoning` (e.g. gpt-4.1 supporting structured
chat completions) and `deskmate-embedding` (text-embedding-3-small); sufficient quota;
and a principal/managed identity. Voice roles: Cognitive Services User + Foundry
User. Text/embedding role: Cognitive Services OpenAI User. Hosting additionally needs
HTTPS/WSS, persistent storage, cookie secret storage and Entra API registration/sign-in.
A project endpoint/hosted Agent, remote MCP, Azure AI Search, separate speech resource
or telephone resource is not required in this design. Model availability must be
checked for the actual region/account before provisioning.

The complete literal first-live environment block and optional dev-key variant are
in [VOICE.md](VOICE.md#azure-resources-and-exact-configuration); `.env.example` lists
all settings. Required core names: AZURE_FOUNDRY_ENDPOINT, AZURE_MODEL_DEPLOYMENT,
AZURE_OPENAI_API_VERSION, AZURE_EMBEDDING_DEPLOYMENT, EMBED_MODEL_NAME,
AZURE_EMBEDDING_DIMENSIONS, AZURE_USE_ENTRA_ID, AZURE_VOICELIVE_ENDPOINT,
AZURE_VOICELIVE_MODEL, AZURE_VOICELIVE_API_VERSION, AZURE_VOICELIVE_VOICE,
VOICE_ENABLED, VOICE_PROVIDER, VOICE_AUTH_MODE, VOICE_TENANT_ID,
VOICE_SESSION_SIGNING_KEY, TEST_MODE, GENERATION_PROVIDER, EMBED_PROVIDER,
DEPLOYMENT_MODE, ALLOWED_ORIGINS, CASE_DB_PATH, CHROMA_DIR and empty optional NEO4J_URI.
Local key mode additionally needs both server-only Azure API keys. Entra deployed
mode additionally needs VOICE_AUTH_TENANT_ID and VOICE_AUTH_AUDIENCE.

## 8. Local fake demo

Run `./scripts/fake-voice-demo.sh`; open http://127.0.0.1:5173 and use **Start text
support** to avoid microphone access. The launcher creates fresh private stores and
explicit TEST_MODE stub/fake providers without editing `.env`. Ingest the fictional
VPN KB, give name/email/callback, confirm each, report an action result, then explicitly
save the summary. Full sample inputs are in [VOICE.md](VOICE.md#local-isolated-fake-demo).
Fake audio is silent PCM and has no real speech recognition/synthesis. Stop with Ctrl+C.

## 9. One live Azure acceptance

Not performed. Follow [the explicit ten-step procedure](VOICE.md#one-explicit-live-azure-acceptance):
provision/authorize resources, use isolated stores and fictional data, verify text
reasoning and embedding first, use one authorized browser tester, explicitly grant
mic permission, verify transcript/spoken fidelity, contact correction/confirmation,
real MCP retrieval, barge-in, mute, typed fallback, failed action/escalation, explicit
success, summary save, disconnect cleanup and restore-disabled configuration.
Callback contact information is stored, never dialed.

## 10. Remaining limitations; emergency stop and rollback

Live regional/model compatibility, Azure authentication/quota, latency, cost and
spoken fidelity remain unproven. Speech audio may precede transcript mismatch
detection. Structural citation checks do not prove semantic entailment. Conservative
action filters can over-escalate. Every turn may make three structured-model calls
plus tool/embedding and speech work. Cancellation cannot instantly stop an already
dispatched synchronous reasoning SDK request; its SDK deadline remains bounded.
English transcription and one standard voice are currently configured. A browser
unable to create 24 kHz capture must use text. No cloud Agent or remote MCP is present.

The deployed API supports Entra bearer exchange, but this demo UI has no MSAL sign-in
flow. Local identity is a loopback-only placeholder. Production needs end-to-end
sign-in, broader tenant/capability authorization, rate limiting, storage encryption,
a complete deletion policy, monitoring and multi-replica storage/session design.
Retention is lazy on next session creation and only expires raw voice transcripts/
candidate quotes; confirmed contacts/evidence/cases remain. No complete scheduled
retention service is implemented. Use one replica for SQLite/local disk.

Emergency: **Stop session**, authenticated POST `/voice/sessions/{id}/stop`, close
the tab, or terminate the host. Disable VOICE_ENABLED and restart to prevent new
sessions. See [rollback procedure](VOICE.md#emergency-stop-and-rollback). Preserve
existing data and `.env`; restore source backup/configuration with voice disabled.
Do not reset collections or restore a database unless intentional data rollback is
needed. No telephone hang-up is involved.

## 11. Existing unrelated repository debt

Global checks still fail outside the new module scope: Ruff **244 findings**,
Black **10 pre-existing files**, mypy (`--follow-imports=skip`) **175 errors in 14
files**, frontend ESLint **17 errors**. Baseline Black had 12 files; two obsolete
provider/test files were removed. These are recorded, not mass-formatted.

Frontend critical advisories include existing vitest/tinypool; other findings include
existing build/runtime transitive packages. Installed Python advisories include
Chroma, PDF parsing, HTTP/crypto and development/obsolete local-model packages.
The five reviewed Azure/voice dependencies (Voice Live, aiohttp, identity, OpenAI
SDK and phonenumbers) returned **zero advisories** in this scan. That is a point-in-time
advisory result, not a security guarantee. Rebuild a clean environment and resolve
applicable advisories before exposing the application. Existing Chroma/PostHog
telemetry compatibility messages and old browser mapping data warnings also remain.
Existing non-voice case/fact APIs lack comprehensive owner/tenant authorization;
this change protects newly introduced voice sessions/cases. Fixture enterprise data
is fictional and is not a live enterprise integration.

### Working-tree inventory

Modified tracked files:

- `.env.example`
- `AGENTS.md`
- `backend/app/adapters/embeddings.py`
- `backend/app/agent/memory.py`
- `backend/app/agent/state.py`
- `backend/app/agent/tools.py`
- `backend/app/core/config.py`
- `backend/app/main.py`
- `backend/app/mcp/client.py`
- `backend/app/mcp/server.py`
- `backend/app/models/dto.py`
- `backend/app/models/provider_factory.py`
- `backend/app/services/capabilities.py`
- `backend/app/store/vector_chroma.py`
- `backend/app/tests/conftest.py`
- `backend/app/tests/run_live_versatility.py`
- `backend/app/tests/scenario_data.py`
- `backend/app/tests/test_cases.py`
- `backend/app/tests/test_embeddings.py`
- `backend/app/tests/test_ingest_integration.py`
- `backend/app/tests/test_ingest_service.py`
- `backend/app/tests/test_provider.py`
- `backend/app/tests/test_retrieve.py`
- `backend/app/tests/test_storage_safety.py`
- `backend/app/tests/test_vector_store.py`
- `docs/RAG.md`
- `docs/README.md`
- `docs/UPGRADE_VALIDATION.md`
- `docs/VERSATILITY_VALIDATION.md`
- `frontend/README.md`
- `frontend/playwright.config.ts`
- `frontend/src/App.test.tsx`
- `frontend/src/App.tsx`
- `frontend/src/styles.css`
- `frontend/tests/e2e.spec.ts`
- `frontend/vite.config.ts`
- `requirements.txt`
- `templates/tier-b-hosted-hybrid.env`

Added files:

- `backend/app/models/instructions.py`
- `backend/app/models/provider_azure_openai.py`
- `backend/app/testing/__init__.py`
- `backend/app/testing/embeddings.py`
- `backend/app/testing/provider_stub.py`
- `backend/app/testing/voice.py`
- `backend/app/testing/voice_reasoning.py`
- `backend/app/tests/test_browser_voice.py`
- `backend/app/tests/test_provider_azure_openai.py`
- `backend/app/tests/test_voice_sdk.py`
- `backend/app/voice/__init__.py`
- `backend/app/voice/api.py`
- `backend/app/voice/auth.py`
- `backend/app/voice/azure_voicelive.py`
- `backend/app/voice/graph.py`
- `backend/app/voice/models.py`
- `backend/app/voice/normalization.py`
- `backend/app/voice/prompts/support_v1.md`
- `backend/app/voice/providers.py`
- `backend/app/voice/repository.py`
- `backend/app/voice/service.py`
- `docs/VOICE.md`
- `docs/VOICE_REVIEW.md`
- `frontend/public/pcm-worklet.js`
- `frontend/src/VoiceSupport.test.tsx`
- `frontend/src/VoiceSupport.tsx`
- `frontend/src/api.ts`
- `frontend/src/voiceAudio.ts`
- `scripts/fake-voice-demo.sh`

Deleted tracked files:

- Previous hosted-generation adapter in `backend/app/models/`
- `backend/app/models/provider_stub.py`
- `backend/app/tests/test_structured_llm.py`
