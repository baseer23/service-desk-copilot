# Azure activation review — 2026-10-13

**Activation is incomplete.** The existing Azure resource, both minimum-capacity
model deployments, subscription alert budget, Entra identity, minimal structured
reasoning, Azure embeddings and fictional KB ingestion are operational. The full
text workflow failed at the minimum reasoning throughput. Voice Live session
creation/configuration succeeded, but silent-input/output transport validation did
not complete within the two permitted attempts. No physical microphone was used.

The application remains running at **http://127.0.0.1:5173** with Azure providers,
local MCP/stores ready, emergency disable active and prominent failure explanations.
**Start voice chat is visible and disabled. It is not ready for the morning microphone
test.** Further billable operations are blocked rather than replaced by a fake.

## 1. Subscription and budget result

Subscription `f4d123f6-dc20-4e1f-9d46-09ae395415f8` is a paid Microsoft Customer
Agreement Azure Plan. Its resource-manager quota identifier is
`PayAsYouGo_2014-09-01`. Subscription and billing-profile spending limits were `Off`
before activation; neither was changed. Billing currency is **CAD**.

The read-only month-to-date query for October 2026 returned no cost rows: **CAD 0
reported**, not proof that no charges exist. Billing data can arrive later. There
were no subscription budgets or budgets in `rg-k_abdulbaseer-0925`. Existing Owner
permissions included subscription budget writes; no role grant was needed.

Created and read-only verified:

| Property | Value |
| --- | --- |
| Name | `deskmate-total-monthly-10-cad` |
| Scope | Entire subscription; no resource/resource-group filters |
| Amount | CAD 10 |
| Recurrence | Monthly, calendar months |
| Start | 2026-10-01T00:00:00Z |
| Expiration | 2027-10-01T00:00:00Z; renew before expiration |
| Actual alerts | 50%, 80%, 100%: CAD 5, 8, 10 |
| Forecast alerts | 80%, 100%: CAD 8, 10 |
| Comparison | GreaterThanOrEqualTo |
| Recipient | `***@kabdulbaseeroutlook.onmicrosoft.com`, privately confirmed to receive email |

Azure CLI 2.91.0 `az consumption budget --help`, `create --help`, and
`create-with-rg --help` were checked. The simple legacy `create` command cannot
supply these five notifications. The documented Consumption REST API was used
through `az rest`; its response was captured privately and only selected fields
were displayed. Command form of the executed operation:

```bash
az rest --method put \
  --url 'https://management.azure.com/subscriptions/f4d123f6-dc20-4e1f-9d46-09ae395415f8/providers/Microsoft.Consumption/budgets/deskmate-total-monthly-10-cad?api-version=2024-08-01' \
  --body @/private/tmp/deskmate-azure-live-20261013-e93pt3j3/subscription-budget.private.json \
  --only-show-errors --output json \
  > /private/tmp/deskmate-azure-live-20261013-e93pt3j3/budget-create.private.json \
  2> /private/tmp/deskmate-azure-live-20261013-e93pt3j3/budget-create.private.error
```

The private payload contains the actual notification address and is deliberately
not copied into this report. All five enabled alerts, amount, scope and recurrence
were verified after creation.

## 2. This is not a hard cap

**Azure cannot guarantee a hard CAD 10 cutoff for this Pay-As-You-Go subscription.**
Cost Management budgets send alerts and do not stop resources. Alert evaluation and
billing have delays. Credit-based Azure spending limits are fixed to the credit
amount, are unavailable for Pay-As-You-Go, and cannot be customized to CAD 10.
No existing spending limit was removed or weakened.

The subscription also already contains a Log Analytics workspace and Application
Insights component. The workspace uses `PerGB2018`, has 30-day retention and no
configured ingestion daily quota (`-1`). These can incur charges independently of
DeskMate. No paid usage appeared in the queried period, but current/future charges
cannot be ruled out. Those resources were not changed.

References: [Azure spending limits](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/spending-limit),
[budgets and alerts](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets),
[budget REST notification schema](https://learn.microsoft.com/en-us/rest/api/consumption/budgets/create-or-update?view=rest-consumption-2024-08-01).

## 3. Application guard status

`AZURE_USAGE_GUARD_ENABLED=true` is configured. The final failure policy also sets
`AZURE_EMERGENCY_DISABLE=true` and persists disable in the local ledger. Setup mode
has returned to false. Merely restarting or changing the environment emergency flag
cannot clear the persistent disable.

The guard implements:

- Shared owner-only SQLite ledger for FastAPI and MCP, with atomic transactions.
- CAD 8 estimated monthly application allowance and an independent CAD 2 setup ceiling.
- Reservations before dispatch; reported token counters finalize text/embedding estimates.
- Retained reservations when failures leave usage unreported; whole-duration voice reservations.
- Accounting inside SDK worker threads even after awaiting tasks are cancelled.
- One active live support connection globally; two connection starts per UTC day,
  including the two setup connections already used; 10-minute interactive limit and
  20 user turns. Setup connections are bounded to 60 seconds.
- Input bounds using conservative UTF-8 byte counts plus schema/protocol allowance;
  configured bound 32,768. Configured output bound 800; readiness/output smoke probes
  used 80. These are local limits, not exact provider tokenization.
- Non-retrying request pacing: reasoning 61 seconds and embeddings 11 seconds,
  matching verified minimum-capacity rate limits.
- One tiny pasted acceptance document, one local authenticated tester, blocked public
  web/PDF/URL ingestion, and no automatic research in live acceptance mode.
- Remaining allowance and safe failure codes in readiness/UI; loopback emergency control.
- Persistent automatic disable when an interactive voice acceptance session closes.

Conservative CAD assumptions: input 10/million tokens, output 40/million tokens,
embeddings 1/million tokens, voice 0.50/minute. They are **not verified account prices
or an exchange-rate conversion**. Actual voice duration and available token counters
are recorded separately from the conservative reservation.

The guard does not cover unrelated Azure resources, taxes, delayed billing,
unreported usage, inaccurate price assumptions, external callers, an altered ledger,
or an already dispatched request finishing before its SDK deadline. It does not
prove an Azure invoice will stay below CAD 8 or CAD 10.

## 4. Deployments and minimum capacities

Both child deployments were created in existing resource
`k-abdulbaseer-newlife-resource`, resource group `rg-k_abdulbaseer-0925`, **eastus2**,
under the subscription above. No new account, project, agent, speech resource,
search service, resource group or provisioned-throughput reservation was created.

| Deployment | Model/version | SKU | Accepted capacity | Verified state |
| --- | --- | --- | --- | --- |
| `deskmate-reasoning` | `gpt-4.1` / `2025-04-14` | GlobalStandard | 1 | Succeeded |
| `deskmate-embedding` | `text-embedding-3-small` / `1` | Standard | 1 | Succeeded |

The catalog omitted a minimum. Azure successfully accepted the smallest positive
capacity, **1**, for both. Their actual deployment rate limits are:

- Reasoning: 1,000 TPM and one request per 60 seconds.
- Embeddings: 1,000 TPM and one request per 10 seconds.

These are throughput allocations, **not spending limits**. The former 50,000 TPM
proposal was not executed. Safe command forms used:

```bash
az cognitiveservices account deployment create \
  --subscription f4d123f6-dc20-4e1f-9d46-09ae395415f8 \
  --resource-group rg-k_abdulbaseer-0925 \
  --name k-abdulbaseer-newlife-resource \
  --deployment-name deskmate-reasoning \
  --model-format OpenAI --model-name gpt-4.1 --model-version 2025-04-14 \
  --sku-name GlobalStandard --sku-capacity 1 --output none --only-show-errors

az cognitiveservices account deployment create \
  --subscription f4d123f6-dc20-4e1f-9d46-09ae395415f8 \
  --resource-group rg-k_abdulbaseer-0925 \
  --name k-abdulbaseer-newlife-resource \
  --deployment-name deskmate-embedding \
  --model-format OpenAI --model-name text-embedding-3-small --model-version 1 \
  --sku-name Standard --sku-capacity 1 --output none --only-show-errors
```

## 5. Authentication and private configuration

Azure CLI/Entra token acquisition passed for both Cognitive Services and AI scopes.
Minimal OpenAI/embedding inference and Voice Live configuration demonstrate basic
resource access. Existing roles sufficed. **No role assignments, provider
registrations, API keys or connection strings were created or retrieved.** Local
providers now explicitly use Azure CLI credentials; hosting retains DefaultAzureCredential.

The OpenAI provider expects a resource origin and is configured with
`https://k-abdulbaseer-newlife-resource.openai.azure.com`. Voice Live expects the
Foundry resource origin and uses
`https://k-abdulbaseer-newlife-resource.services.ai.azure.com`.

Private `.env` was backed up before editing to:
`/private/tmp/deskmate-azure-activation-20261013-nn8sspiz/env.backup.private`.
The backup and updated environment file are owner-only; the backup is outside Git
and `.env` is ignored. A cryptographically generated signing key is stored privately
and never printed. Old stores and other existing resources were not modified.

Fresh private root:
`/private/tmp/deskmate-azure-activation-20261013-nn8sspiz`.
SQLite: `sqlite/cases.db`; Chroma: `chroma`; usage: `sqlite/azure-usage.sqlite3`.
The browser origin is only `http://127.0.0.1:5173`.

## 6. Reasoning validation

One minimal structured Azure reasoning request passed. One controlled application
text question was submitted and failed during classification with the exact safe
code **`rate_limit_exceeded`**. No reasoning retries followed. The canonical prompt
is too demanding for the accepted minimum throughput; waiting between requests
cannot make an oversized request fit a 1,000-TPM allocation.

The smallest next corrective action is to review the structured request token
requirements and revise the reasoning throughput allocation above 1,000 TPM before
another controlled workflow trial. No automatic throughput increase was made:
capacity 1 is the accepted deployment minimum, and the authorized reasoning trial
limit was reached. A higher capacity is not a spending cap either.

## 7. Embedding validation

A short fictional sentence produced a real Azure embedding with **384 dimensions**.
The single authorized KB document then generated one more Azure vector through the
application/MCP path. The persisted collection contains exactly one document, with
`embedding_provider=azure_openai`, and its content matches:

> If a VPN client cannot connect, first verify whether the client reports connected, disconnected, or an authentication error.

There was no customer data, existing-collection reuse or public-web ingestion.

## 8. MCP validation and confirmed fixes

The running FastAPI lifespan owns a real MCP stdio subprocess. Local storage proof
passed; fictional ingestion traversed the normal HTTP → bound tool → MCP route.
The text request failed before retrieval planning, so **end-to-end semantic evidence
retrieval through the text workflow was not validated**. Independent read-only
collection inspection verified the single persisted Azure-vector document.

Confirmed fixes within this work:

1. MCP exported an empty OpenAI key in Entra mode. The actual OpenAI SDK rejected it
   before any Azure request. Empty keys are now omitted in that mode. A real SDK
   constructor regression catches this failure that injected fake clients missed.
2. Synthetic setup initially used speech VAD, then tried switching to manual commit
   after configuration. Azure rejected that switch. Setup now selects manual commit
   in the first SDK update; normal microphone sessions keep server VAD. The fix is
   covered by actual SDK serialization and still needs live revalidation.
3. Azure error identifiers were lost behind embedding/ingestion wrappers. Bounded
   identifiers now survive without exposing raw bodies or source text.
4. Fake browser tests inherited live `.env` investigation limits. The isolated test
   stack explicitly sets its own limits, modes and usage path.
5. Added the shared usage guard, cancellation-safe accounting, emergency persistence,
   request pacing, safe validation history, privacy checks and UI regressions.

## 9. Voice Live transport validation

Two permitted connections were attempted, both configured and cleanly closed:

| Attempt | Synthetic input | Result |
| --- | --- | --- |
| 1 | 100 ms silent PCM16, 24 kHz | `input_audio_buffer_commit_empty` under speech VAD |
| 2 | Manual-mode correction attempted | `turn_detection_type_change_not_allowed` before PCM was sent |

The second script prepared 200 ms of silence, but the configuration failure
prevented sending it. The first-update manual-mode fix was subsequently made and
unit-tested. **No third live connection was made.** No output audio was verified,
no physical microphone was activated, and silence was not presented as successful
speech recognition. Resource/model access and initial configuration are proven;
full input/output transport and microphone fidelity remain open gates.

## 10. Estimated setup usage

Persisted conservative setup estimate: **CAD 1.110658**, below the CAD 2 ceiling.
The monthly estimate is the same; remaining estimated application allowance is
**CAD 6.889342**. Reported aggregate counters: 577 input tokens, 68 output tokens,
645 total tokens. Failed reasoning retained its reservation. Both failed voice
connections retained a one-minute estimate each because no usage response arrived.

These numbers are local estimates, not an invoice or verified current charges.
Check Cost Management again when billing data becomes available; keep this ledger
and the subscription alerts. The two setup connections used the current UTC day's
two-start allowance; that allowance must reset before any additional connection.

## 11. Automated checks

| Check | Final result |
| --- | --- |
| Backend suite | 199 passed, 2 optional Aura skips |
| Targeted guard/Azure/provider/embedding/MCP/voice/scope/web tests | 124 passed |
| Frontend tests | 30 passed |
| Frontend TypeScript/Vite build | Passed |
| Playwright | 12 passed after isolating test policy from private `.env` |
| Scoped Ruff | Passed on changed core/provider/diagnostic/voice/guard-test modules |
| Scoped mypy | Passed on seven core/provider/adapter/readiness modules |
| Scoped Black / ESLint / Prettier | Passed |
| Shell syntax and pip dependency check | Passed |
| git diff --check | Passed |

The initial build exposed an ES2020-incompatible string method and was corrected.
The first Playwright run had one failure from inherited live investigation limits;
all 12 passed after correction. Regular tests use isolated stores and explicit
fake providers only in TEST_MODE and do not make Azure calls or use real microphones.
Existing unrelated repository lint/dependency debt was not reformatted or fixed.

## 12. Sanitized failures and remaining risks

- `rate_limit_exceeded`: full reasoning workflow blocked by minimum throughput.
- `input_audio_buffer_commit_empty`: silence did not produce a committable VAD buffer.
- `turn_detection_type_change_not_allowed`: manual mode must be chosen initially;
  corrected in source, not live-retested after the attempt ceiling.
- Pre-existing Chroma/PostHog compatibility warnings remain; storage checks and
  tests pass. These warnings do not establish telemetry transport succeeded.
- Existing production gates remain: hosted sign-in integration, tenant authorization
  for legacy text cases, abuse limits, retention/encryption and multi-replica storage.

Live runtime logs/API responses were checked for the generated signing key and full
alert address; neither appeared. Only counters, statuses and bounded error identifiers
are persisted in usage/validation records. Credential/token values and raw SDK bodies
were not printed. No production-ready or successful microphone claim is made.

## 13. Current running status

Normal `make dev` is running, with backend on `http://127.0.0.1:8000` and frontend on
**http://127.0.0.1:5173**. It is not `fake-voice-demo.sh`. Runtime is Azure,
`TEST_MODE=false`, local MCP/private stores READY, text_ready=false and voice_ready=false.
Both configuration and persistent emergency disable are active.

A headless inspection of the real URL verified that Start voice chat is visible and
**disabled**, the two validation failures are prominent, and automatic microphone
requests equal **zero**. The loopback emergency API was verified without inference.
Servers remain available while this terminal session and the computer remain running.

## 14. Morning microphone-test steps

**Do not start a microphone test in the current state.** Open
http://127.0.0.1:5173 and review the displayed failures. The next work must revise
reasoning throughput, validate the complete fictional text/evidence path, and retry
the corrected initial manual-mode transport within a newly authorized trial allowance.
Keep the existing budget and usage ledger; do not reset them to bypass limits.

After those gates pass, the UTC daily allowance resets and the emergency state is
intentionally cleared following cost review:

1. Open http://127.0.0.1:5173.
2. Confirm Azure mode, reasoning/embedding/MCP readiness and a transport-validated voice path.
3. Click **Start voice chat** once.
4. Grant microphone permission.
5. Say: “This is a fictional test. My VPN client cannot connect. What should I check first?”
6. Stay within one session, 10 minutes and 20 user turns. Stop afterward; the acceptance
   guard disables Azure usage on closure. Verify the microphone indicator is off.
7. Review returned usage and later Cost Management charges before any further test.

## 15. Emergency stop and rollback

The UI **Disable Azure usage** control persists disable and closes sessions. To
stop the verified owned development processes and disable usage from the terminal:

```bash
./scripts/azure-emergency-stop.sh \
  /private/tmp/deskmate-azure-activation-20261013-nn8sspiz/server-pids.json
```

The script privately backs up `.env` again before setting emergency disable and
checks process identity before sending SIGTERM. Already dispatched synchronous SDK
requests may finish within their deadlines. No telephone hang-up is involved.

To roll back private configuration after stopping, restore
`/private/tmp/deskmate-azure-activation-20261013-nn8sspiz/env.backup.private` to `.env`
and apply owner-only permissions. Use source compatible with that configuration.
Preserve all pre-existing stores and the new usage ledger; source rollback must
preserve the user's other uncommitted work. No automatic source reset was performed.

Optional cleanup commands for the operator after testing, **not executed**:

```bash
az cognitiveservices account deployment delete \
  --subscription f4d123f6-dc20-4e1f-9d46-09ae395415f8 \
  --resource-group rg-k_abdulbaseer-0925 --name k-abdulbaseer-newlife-resource \
  --deployment-name deskmate-reasoning --output none --only-show-errors

az cognitiveservices account deployment delete \
  --subscription f4d123f6-dc20-4e1f-9d46-09ae395415f8 \
  --resource-group rg-k_abdulbaseer-0925 --name k-abdulbaseer-newlife-resource \
  --deployment-name deskmate-embedding --output none --only-show-errors
```

Deleting these deployments does not remove unrelated resource charges. Retain the
subscription budget. No Azure resource was deleted.

## 16. Files and Azure resources changed

Azure mutations were exactly the subscription budget and two child deployments
listed above. No roles, resource-provider registrations or existing parent resources
were changed.

Implementation/configuration files touched in the cost-guard/activation work:

- `.env.example`; private ignored `.env` with external owner-only backup.
- `backend/app/core/config.py`, new `azure_usage.py`, new `azure_errors.py`.
- `backend/app/models/provider_azure_openai.py`, `backend/app/adapters/embeddings.py`.
- `backend/app/mcp/client.py`, `backend/app/mcp/server.py`.
- `backend/app/diagnostics/readiness.py`, `azure_readiness.py`.
- `backend/app/voice/azure_voicelive.py`, `service.py`, `api.py`, `graph.py`.
- `backend/app/tests/conftest.py`, new `test_azure_usage.py`,
  `test_provider_azure_openai.py`, `test_embeddings.py`, `test_voice_sdk.py`,
  `test_azure_readiness.py`.
- `frontend/src/readiness.ts`, `VoiceSupport.tsx`, `VoiceSupport.test.tsx`;
  `frontend/vite.config.ts`, `frontend/playwright.config.ts`.
- `scripts/dev.sh`, `scripts/fake-voice-demo.sh`, new `scripts/azure-emergency-stop.sh`.
- `docs/VOICE.md`, new `docs/AZURE_ACTIVATION_REVIEW.md`.

Private setup scripts/results, usage data, screenshot and logs live only in the
private activation/preparation directories. Other earlier uncommitted repository
work remains intact. **No commit was made during the activation pass.**
