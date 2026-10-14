# Azure-native delivery review — 2026-10-13

DeskMate's local application and Azure-hosted application are operational for
authenticated case management, document storage and knowledge retrieval. Azure AI
generation, embeddings, document analysis and real voice remain paused by the
user's explicit instruction to keep emergency shutdown enabled. This is a bounded
pilot; full enterprise and live-AI acceptance is not complete.

## Confirmed issues and fixes

- Production lacked tenant authority across HTTP, graph execution and MCP. Entra
  signature/audience/issuer/scope/expiry validation and signed per-call authority
  now constrain managed data access. Admin routes require operator authorization.
- Native checkpoints needed durable serialization, idempotent pending writes and
  unambiguous keys. Cosmos checkpoints preserve typed state, reject pickle and
  avoid namespace collisions; legacy migrated keys require matching metadata.
- Voice state needed atomic linkage, owner isolation and callback/cancellation
  protection. Cosmos ETags, partition transactions, leases and terminal-state
  checks protect writes. Managed SDK work runs off the audio event loop; stale
  response creation is suppressed after interruption or stop.
- Ambiguous MCP failures could replay writes or paid retrieval. Retries are now
  restricted to known nonpaid reads. Actual Cosmos SDK retry policy and managed
  identity timeout configuration are tested, including its zero-retry semantics.
- Hosted MCP discarded dependency paths. It now preserves the hosted Python
  library path while retaining the one-task SDK lifetime.
- Document updates needed originals, versioning, safe incremental publication and
  durable jobs. Private Blob, Cosmos job stages and filtered Search publication
  provide these. Unknown paid outcomes require operator review; no fake vectors
  replace unavailable embeddings. SDK serialization tests exercise actual Azure
  Search and Document Intelligence clients.
- The deployment ZIP omitted the required voice prompt. Real hosting failed while
  checkout tests passed. The asset is now included and tested in an actual bundle.
- Container Apps CLI YAML conversion corrupted an identity resource ID containing
  underscores. Deployment uses raw ARM JSON. Image/bootstrap/configuration changes
  receive distinct immutable revision names. Source integrity is checked at boot.
- Normal UI exposed operational details. It now uses product availability and
  readable citations; infrastructure remains in protected administration.
  Failed sign-out/shutdown requests show explicit failure. Paused/hidden/error
  document polling stops instead of indefinitely keeping Consumption awake.

Each code fix has relevant regression coverage. Existing unrelated repository
lint/type debt and the 31 frontend dependency advisories were not broadly rewritten.

## Actual verification

| Check | Result |
| --- | --- |
| Backend, MCP, PDF and regression suite | 253 passed; 2 optional Aura tests skipped |
| Exact targeted checks in `VOICE.md` | 111 passed |
| Frontend unit tests | 38 passed |
| Frontend production build | Passed |
| Isolated Playwright HTTP/MCP stack | 21 passed; generation/audio explicitly simulated |
| Scoped Python/frontend checks; `pip check` | Passed |
| Actual local UI at `http://127.0.0.1:5173` | Case save/reopen, PDF extraction/save/search, no page errors; AI shutdown respected |
| Actual Azure data-plane checks | Database restart read, partition queries, tenant isolation, private Blob download, semantic Search/citations and shutdown passed |
| Actual hosted HTTPS and authentication | Health/frontend 200; anonymous private APIs 401; real Entra JWT, protected APIs and Secure/HttpOnly session exchange passed |
| Actual hosted data operations | Fictional case create/read, plaintext original hash, lexical search/citations passed; `/ask` correctly blocked with 503 |
| Actual hosted browser | Real Entra session, case save/reopen and PDF upload/private-download integrity passed with no page errors |
| Actual Azure redeployment persistence | Previously created case survives the new application revision |
| Real Azure reasoning, new embeddings, OCR and microphone | Not run: explicit shutdown instruction |

Private logs and screenshots remain outside Git. No API keys, access tokens,
connection strings or `.env` contents were printed. Known private values were
scanned against changed files and application bundles without findings. `.env`
matches its pre-task SHA-256. Existing customer stores were not migrated or reset.

## Ten scenario coverage

MFA failure, VPN authentication, Outlook synchronization, network loss, printer
installation, account lockout and remote desktop failure run through the isolated
browser/HTTP/MCP graph stack with deterministic test reasoning. PDF troubleshooting
uses real local extraction, storage and cited retrieval. Multi-turn voice uses
synthetic media and the real WebSocket/MCP plumbing. Restart persistence uses
checkpoint restoration tests, actual Azure new-client reads and case reopening.
These validate application behavior; they are not real Azure model/speech outcomes.

## Resources, data and spending

The actual resource inventory, identity design, migration details and reproducible
commands are in [AZURE_ENTERPRISE.md](AZURE_ENTERPRISE.md). The working host is:

`https://deskmate-f4d123f6-service.jollyhill-53f21e20.eastus2.azurecontainerapps.io`

The approved fictional migration preserves verified SQLite/Chroma backups, copied
cases/facts/events, the complete disabled usage ledger, eight checkpoints and one
real 384-dimensional Azure vector. Missing historical PDF originals cannot be
reconstructed from extracted text; that text is preserved honestly as text.

The CAD 10 alert budget and CAD 8 estimated allowance are unchanged. The ledger
estimate is approximately CAD 1.36 including a CAD 0.25 infrastructure reservation;
approximately CAD 6.64 remains. This is not an invoice reading or a hard cutoff.
Consumption uses scale-to-zero and the shared free grants; continuous heavy traffic
requires a new forecast. No paid dedicated hosting, Search tier, Postgres server,
registry or private endpoint was provisioned. Unused F1 apps are stopped.

## Remaining risks and acceptance

Live reasoning quality, sustained throughput, embeddings, OCR, speech fidelity,
interruption latency and real microphone recovery still need separately authorized
fictional acceptance. Free service limits, cold starts and scale-to-zero do not
provide an enterprise availability/background-processing SLA. Real organization
connectors, tenant onboarding/roles, complete retention/deletion, load/concurrency
and durable centralized telemetry remain deployment gates. Existing dependency
advisories and repository-wide lint/type debt remain tracked debt.

The exact configuration names are in [.env.azure.example](../.env.azure.example).
Production requires the real tenant/application/operator GUIDs and existing Azure
endpoints. Signing material must resolve privately through Key Vault. Do not paste
the Key Vault reference literally into a running process or enable the usage guard
without explicit authorization. Do not retrieve account keys.

For live acceptance: verify current quotas/roles and configuration; use one
authorized tester and fictional documents; obtain separate enablement for bounded
text/embeddings/OCR; verify actual outcomes; obtain microphone authorization;
follow [VOICE.md](VOICE.md)'s single-session procedure; stop and disable again.
No telephone calling capability or live telephone call is involved.

## Emergency stop and rollback

Use the protected operator button/API `POST /admin/azure/usage/disable` to persist
shutdown and close active sessions. If the UI is unavailable, use the private
operator configuration to call `AzureUsageGuard.disable()` through Entra, then
stop the exact host revisions:

```bash
az containerapp revision set-mode --mode multiple \
  -g rg-k_abdulbaseer-0925 -n deskmate-f4d123f6-service \
  --subscription f4d123f6-dc20-4e1f-9d46-09ae395415f8
az containerapp revision list \
  -g rg-k_abdulbaseer-0925 -n deskmate-f4d123f6-service \
  --subscription f4d123f6-dc20-4e1f-9d46-09ae395415f8 \
  --query '[?properties.active].name' -o tsv
# For each exact active name returned above:
az containerapp revision deactivate --revision REVIEWED_ACTIVE_NAME \
  -g rg-k_abdulbaseer-0925 -n deskmate-f4d123f6-service \
  --subscription f4d123f6-dc20-4e1f-9d46-09ae395415f8
```

Verify no active revision remains. For the local owned stack, use
`scripts/azure-emergency-stop.sh PRIVATE_PID_FILE`; it preserves stores while
stopping owned processes. Close the browser tab to release microphone tracks.

Roll back code by deploying the previous reviewed private bundle/configuration
with shutdown enabled. Preserve current Cosmos, Blob, Search and usage records.
Local rollback retains `.env` and original SQLite/Chroma paths and uses `make dev`.
Migration record rollback is conditional on original ETags/payload hashes and never
deletes the financial ledger. Artifact cleanup requires separate review.

## Files changed

- `.env.azure.example`
- `AGENTS.md`
- `backend/app/agent/azure_checkpoints.py`
- `backend/app/agent/checkpoints.py`
- `backend/app/core/authentication.py`
- `backend/app/core/azure_managed_usage.py`
- `backend/app/core/azure_usage.py`
- `backend/app/core/config.py`
- `backend/app/core/principal.py`
- `backend/app/diagnostics/azure_connectivity.py`
- `backend/app/diagnostics/host_startup.py`
- `backend/app/diagnostics/workflow_request_size.py`
- `backend/app/main.py`
- `backend/app/mcp/client.py`
- `backend/app/mcp/server.py`
- `backend/app/models/provider_azure_openai.py`
- `backend/app/rag/retrieve.py`
- `backend/app/repositories/azure_memory.py`
- `backend/app/services/azure_document_formats.py`
- `backend/app/services/azure_documents.py`
- `backend/app/services/azure_migration.py`
- `backend/app/services/capabilities.py`
- `backend/app/services/document_worker.py`
- `backend/app/store/azure_knowledge.py`
- `backend/app/store/azure_records.py`
- `backend/app/testing/cosmos.py`
- `backend/app/tests/test_azure_data_platform.py`
- `backend/app/tests/test_azure_migration_documents.py`
- `backend/app/tests/test_azure_readiness.py`
- `backend/app/tests/test_azure_sdk_data.py`
- `backend/app/tests/test_azure_usage.py`
- `backend/app/tests/test_enterprise_auth.py`
- `backend/app/tests/test_hosted_bundle.py`
- `backend/app/tests/test_local_documents.py`
- `backend/app/tests/test_managed_limits.py`
- `backend/app/tests/test_managed_voice_io.py`
- `backend/app/tests/test_mcp.py`
- `backend/app/tests/test_startup_diagnostics.py`
- `backend/app/voice/api.py`
- `backend/app/voice/auth.py`
- `backend/app/voice/azure_repository.py`
- `backend/app/voice/azure_voicelive.py`
- `backend/app/voice/service.py`
- `docs/AZURE_ENTERPRISE.md`
- `docs/AZURE_ENTERPRISE_PLAN.md`
- `docs/AZURE_ENTERPRISE_REVIEW.md`
- `docs/VOICE.md`
- `docs/VOICE_REVIEW.md`
- `frontend/package-lock.json`
- `frontend/package.json`
- `frontend/playwright.config.ts`
- `frontend/src/AdminConsole.test.tsx`
- `frontend/src/AdminConsole.tsx`
- `frontend/src/App.test.tsx`
- `frontend/src/App.tsx`
- `frontend/src/AuthGate.test.tsx`
- `frontend/src/AuthGate.tsx`
- `frontend/src/InvestigationPanels.test.tsx`
- `frontend/src/InvestigationPanels.tsx`
- `frontend/src/KnowledgePanel.test.tsx`
- `frontend/src/KnowledgePanel.tsx`
- `frontend/src/VoiceSupport.test.tsx`
- `frontend/src/VoiceSupport.tsx`
- `frontend/src/api.test.ts`
- `frontend/src/api.ts`
- `frontend/src/main.tsx`
- `frontend/src/readiness.ts`
- `frontend/src/voiceErrors.ts`
- `frontend/tests/e2e.spec.ts`
- `frontend/vite.config.ts`
- `requirements-azure.txt`
- `requirements.txt`
- `scripts/configure_azure.py`
- `scripts/container_bootstrap.py`
- `scripts/deploy_containerapps.py`
- `scripts/initialize_azure_data.py`
- `scripts/migrate_azure.py`
- `scripts/package_azure.py`
- `scripts/provision_azure.py`
