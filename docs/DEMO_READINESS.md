# DeskMate demo repair and acceptance — 2026-10-14

This report covers the Azure demo activation and the subsequent microphone/UI
repair. The current implementation retains LangGraph orchestration, MCP tool
authorization, Entra/managed identity, existing knowledge, and persistent usage
controls. No telephone calls were placed. Existing stores were not reset.

## Confirmed defects and fixes

| Defect observed | Fix and regression coverage |
| --- | --- |
| Microphone frames were discarded while a live greeting was generated. | Azure's configured acknowledgement now enables input immediately. Greeting generation runs separately; user speech supersedes it, including speech during a managed-state read. Startup and cancellation regressions cover this. |
| The UI reported listening without establishing capture or forwarding. | Listening requires a running audio context, a live track, recent worklet frames, a configured socket and an SDK-forwarding acknowledgement. A microphone meter and input-device selector expose actual capture activity. Readiness also checks the full speech reservation. |
| Browsers running at a native rate other than 24 kHz were rejected. | The real worklet converts the actual context rate into mono 24 kHz PCM16LE, with phase preserved between render blocks. Tests exercise 24, 44.1 and 48 kHz, 960-byte frames, sample values and byte order. |
| Generic Realtime response options serialized successfully but were rejected by Voice Live. | Use the supported `pre_generated_assistant_message` renderer input. Official SDK serializers, typed events and real Azure output were checked. |
| Late response completion could be attributed to a newer reply. | Preserve nested response IDs and correlate response epochs. Ignore cancelled/stale output and completions. The application owns VAD interruption to avoid double cancellation. |
| Heartbeats, callbacks and HTTP Stop could race Cosmos ETags. | Share the runtime lock across lease renewal, turn updates, HTTP Stop and connection cleanup. Repeated Stop is idempotent. A real stop conflict was reproduced; the repaired live test returned HTTP 200. |
| Playback cancellation waited for a database write. | Clear playback and request provider cancellation before persisting interruption state. Ordering regression verifies this. |
| Managed storage and accounting could block the audio event loop. | Offload session creation/inspection, readiness, accounting, case visibility and cleanup. Regression checks event-loop responsiveness during a slow managed read. |
| The 16-frame input queue could overflow at ordinary managed-service latency. | Use a bounded 128-frame queue, approximately 2.56 seconds at the normal frame size. Overflow fails explicitly. |
| “Yes, that is correct.” failed contact confirmation. | Normalize punctuation and accept a bounded list of affirmative/negative phrases. Corrections such as “Yes, but that email is wrong” do not confirm. |
| Local Azure voice cases could lose their signed owner authority at the MCP boundary. | Scope native voice-case access with the verified actor, while preserving team investigation access and hiding other owners' voice cases. |
| Cosmos checkpoint writes amplified RU use; usage inspection competed for writer leases. | Batch pending checkpoint channels per task, preserve legacy reads, and inspect committed usage without a writer lease. Failed transactions discard tentative state and release only their own lease. |
| General chat, schema-invalid plans, question verification and nullable search scores failed real workflows. | Add a compiled general-chat node with bounded history/owner checks, bounded schema correction, independent clarification verification and nullable score handling. Knowledge requests still avoid case writes. |
| The dashboard exposed every panel and long repetitive histories. | Four focused, accessible views; searchable/paginated history; status and provenance filters; document drop area and searchable library; dedicated voice transcript and controls. Known samples remain available. No records are deleted. Explicit provenance survives investigation. |

Unused read-tool schemas are now omitted from contact extraction and reply
generation. Planning still receives and validates the discovered MCP schemas.
This reduces prompt size; it does not establish a particular latency improvement.

The Voice Live wire fields and VAD configuration follow Microsoft's
[2026-04-10 API reference](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-api-reference-2026-04-10).
VAD uses a 0.4 threshold, 400 ms prefix padding, 500 ms silence detection,
`create_response=false` and application-owned interruption.

## Real acceptance evidence

Private evidence is under `/private/tmp/deskmate-repair-jqryhn51`; it is excluded
from Git and deployment packages.

| Check | Observed result |
| --- | --- |
| Physical Chrome capture through the actual `VoiceAudio` worklet | One real track, 150 PCM frames, 144,000 bytes, 55 non-silent frames, 24 kHz context. Mute suppressed transmission, unmute resumed it, and close stopped the track. No raw microphone recording was saved. |
| Real Azure ASR and LangGraph responses using bounded prerecorded fictional speech | 374 input frames, 217 non-silent frames, 357 acknowledged forwarded frames, two VAD starts and two stops. Azure transcribed “Hi DeskMate, my VPN is not connecting” and “It says authentication failed.” |
| Two consecutive spoken turns | DeskMate asked for the exact VPN error, then asked whether the account was locked or other company services were accessible. Both replies were generated through the actual investigation graph and spoken by Azure. |
| Barge-in and termination | Two playback clears, output interruption on the second utterance, 20 output audio chunks, no provider errors, Stop HTTP 200. |
| Real UI navigation | All four views selected correctly; no JavaScript exceptions, visible alerts or mobile horizontal overflow. |
| Azure scanned-PDF ingestion | Fictional scanned PDF passed Blob upload, Document Intelligence OCR, Azure embedding and Search indexing: one page/chunk, original hash matched, four cited keyword hits. |
| Text workflows and persistence | Real Toronto/document answers, VPN/MFA/Outlook/printer clarification, persisted case reopen and restart/resume were exercised. |

The Azure speech fixture was generated with macOS speech synthesis. It was not a
human microphone utterance. Physical capture and cloud recognition were tested
separately. A person speaking naturally through the complete browser session is
the remaining demonstration acceptance step. Live partial-transcription counts
were not captured in the final fixture report; delta handling has SDK/UI tests.

The bounded cloud conversation took approximately 58 seconds including connection
and two turns. Model-generated replies had noticeable latency, approximately
15–30 seconds in this run. The unused-schema reduction is regression-tested but
has not been timed against another paid session. Do not promise immediate answers.

## Configuration and provisioned resources

The explicitly approved estimate remains **CAD 150**, including a **CAD 90 monthly
infrastructure forecast**. Historical charges, one concurrent session, twenty
daily sessions, twenty turns and emergency shutdown are preserved. CAD 10 Azure
budget alerts remain. Estimates are not an Azure invoice cap.

Validation exhausted most of the application estimate. Voice sessions were
tightened to two minutes so a demo can fit the remaining allowance; the normal
ten-minute reservation requires CAD 5. A proposed CAD 175 total allowance has not
been applied without separate approval. Use protected `/admin/readiness` to
inspect current estimates before the demo.

Production uses these existing resources in subscription
`f4d123f6-dc20-4e1f-9d46-09ae395415f8`, group `rg-k_abdulbaseer-0925`:

| Resource | Configuration |
| --- | --- |
| Foundry `k-abdulbaseer-newlife-resource` | East US 2, AIServices S0, `gpt-realtime` Voice Live access. |
| `deskmate-reasoning` | `gpt-4.1`, version 2025-04-14, GlobalStandard capacity 200. |
| `deskmate-embedding` | `text-embedding-3-small`, version 1, Standard capacity 100; 384 output dimensions. |
| `deskmate-f4d123f6-cosmos` | East US 2, free-tier account, NoSQL `deskmate` database, `state` container partitioned by `/tenant_id`, shared database throughput 2,000 RU/s. |
| `deskmate-f4d123f6-search` | Central US, Free tier, hybrid/vector/semantic knowledge index. |
| `dskf4d123f6store` | East US 2, LRS Hot, private `documents` container, shared-key access disabled. |
| `deskmate-f4d123f6-docs` | East US 2, Document Intelligence F0, Entra authentication. |
| `deskmate-f4d123f6-kv` | East US 2, RBAC Key Vault, existing session signing secret. |
| `deskmate-f4d123f6-runtime` | User-assigned identity with scoped Blob, Search, Key Vault, cognitive and Cosmos permissions. |
| `deskmate-f4d123f6-env` / `deskmate-f4d123f6-service` | East US 2, Consumption Container Apps, 0.5 vCPU/1 GiB, zero-to-one replicas. |
| Entra API/browser registration | Validated audience, tenant, expiry and delegated scope; browser sign-in and signed owner cookies. |

At the retrieved CAD retail rate, the extra 1,000 RU/s above the free allocation
is approximately CAD 0.113/hour, or CAD 84.07 over 744 hours. This fits the approved
CAD 90 forecast and continues until throughput is reduced. See
[Cosmos free tier](https://learn.microsoft.com/en-us/azure/cosmos-db/free-tier) and
[provisioned-throughput pricing](https://azure.microsoft.com/en-us/pricing/details/cosmos-db/standard-provisioned/).

Required application variables are listed below. Actual values remain private.
Do not copy `.env` into a deployment bundle or retrieve API keys.

```dotenv
TEST_MODE=false
DATA_PLATFORM=azure
DEPLOYMENT_MODE=local
GENERATION_PROVIDER=azure_openai
EMBED_PROVIDER=azure_openai
VOICE_ENABLED=true
VOICE_PROVIDER=azure_voicelive
VOICE_AUTH_MODE=local_demo
VOICE_TENANT_ID=<approved-tenant-id>
VOICE_SESSION_SIGNING_KEY=<persistent-private-signing-secret>
ALLOWED_ORIGINS=http://127.0.0.1:5173
AZURE_USE_ENTRA_ID=true
AZURE_FOUNDRY_ENDPOINT=https://<foundry-resource>.openai.azure.com
AZURE_MODEL_DEPLOYMENT=deskmate-reasoning
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_EMBEDDING_DEPLOYMENT=deskmate-embedding
EMBED_MODEL_NAME=text-embedding-3-small
AZURE_EMBEDDING_DIMENSIONS=384
AZURE_VOICELIVE_ENDPOINT=https://<foundry-resource>.services.ai.azure.com
AZURE_VOICELIVE_MODEL=gpt-realtime
AZURE_VOICELIVE_API_VERSION=2026-04-10
AZURE_VOICELIVE_VOICE=en-US-AvaNeural
AZURE_COSMOS_ENDPOINT=https://<cosmos-account>.documents.azure.com:443/
AZURE_COSMOS_DATABASE=deskmate
AZURE_COSMOS_CONTAINER=state
AZURE_STORAGE_ACCOUNT_URL=https://<storage-account>.blob.core.windows.net
AZURE_STORAGE_CONTAINER=documents
AZURE_SEARCH_ENDPOINT=https://<search-resource>.search.windows.net
AZURE_SEARCH_INDEX=<configured-index-name>
AZURE_DOCUMENT_ENDPOINT=https://<document-resource>.cognitiveservices.azure.com
AZURE_DOCUMENT_API_VERSION=2024-11-30
AUTH_ALLOWED_TENANTS=<approved-tenant-id>
AZURE_USAGE_GUARD_ENABLED=true
AZURE_EMERGENCY_DISABLE=false
AZURE_LIVE_TEST_MODE=false
AZURE_SETUP_VALIDATION_MODE=false
AZURE_ESTIMATED_MONTHLY_APP_LIMIT_CAD=150
AZURE_ESTIMATED_INFRASTRUCTURE_CAD=90
AZURE_MAX_LIVE_SESSIONS_PER_DAY=20
AZURE_MAX_CONCURRENT_SESSIONS=1
AZURE_MAX_VOICE_SESSION_MINUTES=2
AZURE_MAX_TURNS_PER_SESSION=20
AZURE_REASONING_MIN_INTERVAL_SECONDS=1
AZURE_EMBEDDING_MIN_INTERVAL_SECONDS=1
KNOWLEDGE_SCOPE=broad
ENABLE_LOCAL_FIXTURES=false
```

Confirm variable names against `.env.example`; production additionally requires
`DEPLOYMENT_MODE=production`, `VOICE_AUTH_MODE=entra`, `AUTH_CLIENT_ID`,
`AUTH_ALLOWED_TENANTS`, operator IDs where administration is required, host origin,
`AZURE_CLIENT_ID` and Key Vault signing-secret
reference. Local storage paths remain configured privately for rollback. No new
telephone/ACS resource or destination allowlist is required for browser voice.

## Human demonstration procedure

1. Open `http://127.0.0.1:5173` consistently, or sign into the hosted application.
   Check availability and the protected allowance before starting a paid session.
2. In Chat, send a general question; follow up in the same conversation. Start a
   separate fictional IT issue and answer its clarification question.
3. In Investigations, search/reopen that case. Use “Demo and tests” to inspect
   explicitly labeled fixtures; verify normal history does not show them by default.
4. In Knowledge, drop an authorized fictional PDF, add it, wait for Ready, search
   its contents and open its source. Re-upload the same file to verify deduplication.
5. In Voice, select the intended microphone and start. Allow microphone permission
   once. Wait for Listening and confirm that the activity meter moves.
6. Say “Hi DeskMate, my VPN is not connecting.” Verify the visible transcript and
   spoken question. Say “It says authentication failed.” Verify that the next
   response uses that context.
7. Speak during a reply; output should stop and the new utterance should appear.
   Mute, confirm transmission stops, unmute and continue without another prompt.
8. Confirm fictional contact details explicitly if saving a voice summary. Save,
   reopen the linked case, and verify owner-only access.
9. End the session. Confirm microphone capture ends, controls reset, and a new
   session starts fresh. A disconnected session may resume through text or voice;
   an explicitly stopped session cannot be resumed.

## Emergency stop and rollback

- The visible **Stop session** control closes the microphone/socket immediately
  and requests a durable stop. Browser voice is not a telephone call.
- An authenticated operator can POST `/admin/azure/usage/disable` from the same
  origin. The shared persistent shutdown blocks both local and hosted paid work.
  The loopback-only `/azure/usage/disable` remains available to the local operator.
- `scripts/azure-emergency-stop.sh` accepts the private owned-process PID file.
  Use it for an actual emergency; it preserves application data and disables usage.
- Keep prior immutable deployment bundles and the private pre-demo configuration.
  Redeploy with shutdown enabled for rollback. Never reset Cosmos, Blob, SQLite or
  Chroma. Older backends that do not understand the new policy table need careful
  policy preservation; prefer the current backend with the previous UI if needed.
- To reduce ongoing Cosmos throughput after the demo, use the explicitly reviewed
  resource change below; this is not performed automatically:

```bash
az cosmosdb sql database throughput update \
  --subscription f4d123f6-dc20-4e1f-9d46-09ae395415f8 \
  --resource-group rg-k_abdulbaseer-0925 \
  --account-name deskmate-f4d123f6-cosmos \
  --name deskmate --throughput 1000
```

## Verification and remaining debt

The recorded checks include the full backend suite, the targeted suite in
`VOICE.md`, real MCP stdio, PDF/embedding/managed-store tests, frontend unit tests,
production build and all 21 isolated Playwright tests. Final totals: **289 backend tests passed, two explicitly skipped; 120 targeted
backend/MCP tests passed; 49 frontend tests passed; 21 Playwright tests passed**.
The production build and `pip check` pass. The release SHA is reported with delivery.

Pre-existing debt is separate from these repairs: the npm audit reports 31
existing dependency findings (including two critical); repository-wide style/type
debt remains outside this focused repair. Aura integrations are explicitly skipped
without their isolated credentials. Production operations still need tenant
retention/deletion policy and broader load evaluation. The budget is finite and
nearly consumed; do not bypass it to demonstrate readiness.

## Files changed

```text
.env.example
AGENTS.md
backend/app/agent/azure_checkpoints.py
backend/app/agent/graph.py
backend/app/agent/models.py
backend/app/agent/state.py
backend/app/agent/tools.py
backend/app/core/azure_managed_usage.py
backend/app/core/azure_usage.py
backend/app/core/config.py
backend/app/diagnostics/readiness.py
backend/app/main.py
backend/app/mcp/server.py
backend/app/models/dto.py
backend/app/models/instructions.py
backend/app/models/provider_azure_openai.py
backend/app/store/azure_knowledge.py
backend/app/store/azure_records.py
backend/app/tests/conftest.py
backend/app/tests/test_azure_data_platform.py
backend/app/tests/test_azure_readiness.py
backend/app/tests/test_azure_sdk_data.py
backend/app/tests/test_azure_usage.py
backend/app/tests/test_browser_voice.py
backend/app/tests/test_cases.py
backend/app/tests/test_general_conversations.py
backend/app/tests/test_local_managed_voice_owner.py
backend/app/tests/test_managed_voice_io.py
backend/app/tests/test_voice_sdk.py
backend/app/voice/api.py
backend/app/voice/azure_repository.py
backend/app/voice/azure_voicelive.py
backend/app/voice/graph.py
backend/app/voice/normalization.py
backend/app/voice/service.py
docs/DEMO_READINESS.md
docs/VOICE.md
docs/VOICE_REVIEW.md
frontend/playwright.config.ts
frontend/public/pcm-worklet.js
frontend/src/App.test.tsx
frontend/src/App.tsx
frontend/src/InvestigationHistory.test.tsx
frontend/src/InvestigationHistory.tsx
frontend/src/KnowledgePanel.tsx
frontend/src/VoiceSupport.test.tsx
frontend/src/VoiceSupport.tsx
frontend/src/api.test.ts
frontend/src/api.ts
frontend/src/styles.css
frontend/src/voiceAudio.test.ts
frontend/src/voiceAudio.ts
frontend/tests/e2e.spec.ts
scripts/deploy_containerapps.py
```
