# Azure-native pilot: implementation, evidence and operating limits

This extends repair checkpoint `2ce47a2`. It preserves local stores and makes Azure
the required production data platform. It is a constrained pilot, not a certified
enterprise deployment. Live AI validation remains blocked by the user's explicit
instruction to keep the persistent emergency shutdown enabled.

## Actual provisioned foundation

Subscription `f4d123f6-dc20-4e1f-9d46-09ae395415f8`, group
`rg-k_abdulbaseer-0925`:

| Resource | Actual selection | Region |
| --- | --- | --- |
| `deskmate-f4d123f6-cosmos` | Cosmos NoSQL, lifetime free tier, one shared 1,000 RU/s database `deskmate`, container `state`, `/tenant_id` partition key, local key auth disabled | East US 2 |
| `deskmate-f4d123f6-search` | AI Search Free, free semantic billing, Entra only, `knowledge` index with 384-dimensional HNSW vectors and semantic configuration `grounded` | Central US |
| `dskf4d123f6store` | StorageV2 Standard LRS Hot, private `documents` container, shared-key/public-blob access disabled | East US 2 |
| `deskmate-f4d123f6-kv` | Key Vault Standard, RBAC, persistent session signing secret | East US 2 |
| `deskmate-f4d123f6-docs` | Document Intelligence F0, custom endpoint, Entra authentication | East US 2 |
| `deskmate-f4d123f6-free-plan` | App Service Linux F1, 1 worker | Central US |
| `deskmate-f4d123f6-app` | Python 3.12 app, system identity, HTTPS/TLS 1.2, publishing basic auth disabled | Central US |
| `deskmate-f4d123f6-host` | Fresh F1 diagnosis app; stopped with the original app after successful Consumption hosting | Central US |
| `deskmate-f4d123f6-env` | Container Apps Consumption environment; no dedicated nodes, registry or Log Analytics workspace | East US 2 |
| `deskmate-f4d123f6-runtime` | User-assigned managed identity; resource-scoped data roles | East US 2 |
| `deskmate-f4d123f6-service` | HTTPS Container App, Python 3.12 pinned image, 0.5 vCPU/1 GiB, minimum 0 and maximum 1 replica | East US 2 |

Existing Foundry `k-abdulbaseer-newlife-resource` is reused. Its GPT-4.1
`deskmate-reasoning` GlobalStandard allocation is verified at **40,000 TPM / 40
requests per minute**. Its `text-embedding-3-small` Standard deployment
`deskmate-embedding` is verified at **10,000 TPM / 10 requests per 10 seconds**.
These are consumption deployments, not provisioned-throughput subscriptions.
The resource's existing shared key configuration was not changed; this app uses
Entra, and no account/API keys were retrieved.

East US 2 rejected Free Search for capacity and F1 hosting for zero regional
quota. Central US accepted both free tiers. No paid fallback was provisioned.

An Entra SPA/API application exposes delegated `DeskMate.Access`, issues v2
tokens, and preauthorizes Azure CLI for operator testing. Runtime identities have
resource-scoped Blob/Search/Key Vault/cognitive-service data roles and a
Cosmos data role scoped to the state container. No client secret was created.
The host's Key Vault reference was verified **Resolved**.

## Architecture and implemented behavior

React → authenticated FastAPI → one LangGraph supervisor → bound LangChain
tools → lifespan-owned MCP stdio → managed capabilities. Foundry Agent Service
and an extra Speech resource were not added: they would duplicate orchestration
or speech already provided by LangGraph and Voice Live.

Production uses tenant-partitioned Cosmos records for cases, facts, events,
checkpoints, voice conversations and durable document jobs. A signed per-call MCP
authority carries tenant/owner identity outside model-controlled arguments.
Checkpoint writes retain idempotent task indexes and reject namespace aliasing.
Voice case linkage and its confirmation event use a same-partition transactional
batch with ETags. Terminal states, owner leases and replay IDs are enforced.

The document pipeline saves immutable originals and extracted/vector artifacts
in private Blob, stages jobs in Cosmos, uses Document Intelligence layout/OCR,
chunks by page, requests real Azure embeddings and publishes to Search. PDFs are
split into two-page F0 requests; bounded Word packages preserve paragraphs and
tables. Embedded Word images are unsupported by the service. Multi-page TIFF
files over two pages must be converted to PDF. Local PDF text extraction remains
available. Layout-based classification is implemented; a trained custom document
classifier is not provisioned or validated.

Exact byte SHA-256 provides deduplication. Reuploading a changed document with the
same normalized title creates a version. Previously published content remains
available until the replacement is published. Search excludes superseded versions
while original downloads remain available. Unknown extraction/embedding outcomes
require operator review instead of automatic repeat billing. A crashed worker
resumes safe queued stages after restart. Consumption scales to zero when idle;
requests wake the host and pending work resumes. It provides no guarantee that a
background document job will finish while the application is idle. Document
polling stops during disabled processing, hidden tabs and polling failures.

The public interface displays investigations, documents, readable citations and
support availability. Provider codes, deployment/model IDs, ledger costs and
infrastructure diagnostics are confined to `/admin` and `/admin/readiness`.
Production disables API documentation, exposes a minimal health response, and
requires Entra for data routes. Public web research and fictional organization
fixtures are disabled. Actual organization/inventory connectors are not configured.

## Verified evidence and limits

Actual Azure data-plane probes passed: fresh managed-database write/read through
a new client, partitioned queries, cross-tenant read isolation, private original
download, semantic knowledge search and citations. They dispatched no paid AI.

Only the previously approved fictional isolation stores were migrated. Private
SQLite backups use the online backup API and integrity checks; Chroma snapshots
and SHA manifests preserve originals. Case/fact/event records, the complete
emergency-disabled usage ledger, one real 384-dimensional Azure vector and eight
LangGraph checkpoints were copied and checked. Earlier customer stores were not
uploaded, modified or deleted. Legacy local staging stored extracted text rather
than PDF originals; the migration preserves that text as text and cannot recreate
missing source PDFs. Voice transcripts with contacts require explicit data
authorization and owner mapping before migration; the approved source had none.

Eight isolated multi-turn graph/MCP scenarios measured a worst conservative
request allocation estimate of **7,548 tokens**, an 8,000 TPM single-request floor.
The deployed cadence proposes five requests/minute against 40,000 TPM. This uses
explicit deterministic test reasoning to measure actual graph contexts; it does
not validate Azure reasoning quality or sustained live load.

Real text reasoning, new embeddings, Azure OCR, real microphone transcription,
spoken replies and live multi-turn voice were **not run** during this transformation
because emergency shutdown remains binding. Earlier minimal Azure probes are not
evidence that these new complete workflows passed.

Hosting endpoint:
`https://deskmate-f4d123f6-service.jollyhill-53f21e20.eastus2.azurecontainerapps.io`.
Actual HTTPS health and frontend return 200; anonymous private routes return 401.
Real Entra JWT validation and Secure/HttpOnly cookie exchange pass. Authenticated
case/document/admin APIs pass; fictional case creation, document original integrity
and lexical search with citations pass. Reasoning returns 503 with shutdown enabled.

The first packages omitted `voice/prompts/support_v1.md`, causing hosted startup
failure despite checkout-based tests passing. The packager now preserves that
runtime asset and tests the actual repository bundle. Immutable revision names
include image/bootstrap/configuration changes. The Container Apps CLI YAML identity
conversion also corrupted underscores in the group ID; raw ARM JSON preserves the
reviewed identity map. Temporary diagnostics are disabled in the release. Unused
App Service apps are stopped and retained for rollback.

## Financial boundaries

The CAD 10 subscription alert budget and CAD 8 conservative application allowance
remain unchanged. The complete persistent shutdown ledger remains disabled.
CAD **0.25/month** of the existing allowance is reserved for infrastructure, also
in new months; this is not an extra allowance or an invoice cap.

On 2026-10-13, CAD retail prices returned Linux F1 0/hour; B1 0.0241/hour
(approximately **CAD 17.59/month**, beyond authorization); LRS Hot Blob storage
0.0261/GB-month; Blob writes 0.0708/10,000; Key Vault operations 0.0425/10,000.
Blob data is bounded to 100 MB/1,000 artifacts and 10,000 reads/writes per month.
Tenant records are bounded to 100 MB/5,000 objects, at most eight configured tenants.
Document analysis uses F0 only, conservatively reserving up to 500 pages/month.
No paid Search, SQL/Postgres server, registry, private endpoint or B1 upgrade was created.
Existing unrelated monitoring charges and delayed Azure billing remain outside the
application guard; budget alerts cannot guarantee an invoice cutoff.

Consumption's subscription-shared monthly grants are 180,000 vCPU-seconds,
360,000 GiB-seconds and 2 million HTTP requests. At 0.5 vCPU/1 GiB this covers up to
100 active replica-hours if no other workload consumes the grant. The bounded
acceptance checks fit this forecast; continuous or heavy traffic needs a new cost
assessment. Scale-to-zero adds no fixed hosting baseline, but it is not an invoice
cutoff. Deployment bundles are additional small storage consumption beyond the
document quota; retain reviewed rollback artifacts and review cleanup separately.

## Reproducible operator workflow

Use Python 3.12 and the repo `.venv`. Install `requirements.txt` locally; hosted
packages use the directly pinned `requirements-azure.txt`. Keep `.env` private.
The nonsecret template is [.env.azure.example](../.env.azure.example).

1. `PYTHONPATH=. .venv/bin/python scripts/provision_azure.py --plan` shows exact
   reviewed commands. Provision only available free/low-cost selections; never
   silently substitute a paid SKU.
2. `scripts/configure_azure.py` configures `database`, `hosting`, `identity` and
   `data` stages with a fresh private output directory. Resolve role propagation
   before proceeding. CLI metadata goes to private files, never terminal secrets.
3. `scripts/initialize_azure_data.py --configuration PRIVATE_DIRECTORY` creates
   the private container/index and transfers the existing signing secret directly
   to Key Vault in memory. Never use key listing/publishing profiles.
4. `scripts/migrate_azure.py --configuration PRIVATE_DIRECTORY --output FRESH_BACKUP`
   defaults to backup only. `--apply-approved-fictional-data` is restricted to the
   originally approved isolated fictional root. Review manifests privately.
5. `PYTHONPATH=. .venv/bin/python -m backend.app.diagnostics.azure_connectivity
   --configuration PRIVATE_DIRECTORY` runs low-cost data checks without AI.
6. `npm --prefix frontend run build`, then
   `.venv/bin/python scripts/package_azure.py --output PRIVATE_PACKAGE.zip`.
   The package excludes `.env`, local databases, Chroma, test artifacts and fake
   providers. Scan for known secrets before deploying through Entra.
7. Configure the host using the generated nonsecret settings and Key Vault
   reference. Keep `AZURE_EMERGENCY_DISABLE=true`. Enforce TLS, one free worker,
   disabled publishing basic auth, no access/request-body logging.
8. For the working Consumption host, download Linux CPython 3.12 wheels using
   `pip download --only-binary=:all:` with reviewed manylinux platform tags, then
   package with `scripts/package_azure.py --linux-wheels PRIVATE_WHEELS
   --output PRIVATE_PACKAGE.zip`. Resolve the official Python 3.12 base image to
   a Linux/amd64 digest. Use `scripts/deploy_containerapps.py --private-output
   PRIVATE_ROOT --bundle PRIVATE_PACKAGE.zip --image REVIEWED_IMAGE_DIGEST
   --stage roles`, then `--stage deploy`. The bootstrap downloads only the hashed
   private bundle through managed identity and verifies its digest/path/size.
   It installs no dependencies at runtime and uses no registry credentials or SAS.
   Check actual HTTPS health and protected APIs, not just ARM provisioning status.

Operator-only `--startup-diagnostic` publishes at most three bounded Python stacks
to the private deployment Blob. It includes no frame locals or environment values.
Do not enable it in the ordinary release; console/startup artifacts remain private.

Only after separate explicit enablement may live fictional tests run. Configure
reasoning and embedding deployments, prove their actual results, then obtain
separate microphone authorization. Preserve session/day/turn limits and disable
again after acceptance. No telephone call is part of this procedure.

## Rollback and emergency stop

The operator API `/admin/azure/usage/disable` persists shutdown and closes active
conversations. If the host is unavailable, construct the managed `AzureUsageGuard`
with the private operator configuration and call `disable()` through Entra, then
stop active Container Apps revisions for this exact app/group/subscription. Set
revision mode to Multiple if necessary, list revisions, and deactivate each active
revision. Verify none remains active; preserve data and the usage ledger. The two
unused App Service apps can remain stopped. Neither operation deletes stores or
restores stale estimates.

Redeploy the previous reviewed ZIP and startup/settings revision to roll back
code. For local rollback, retain `.env` and existing SQLite/Chroma paths and run
`make dev`; restore from verified snapshots only after explicit operator review.
`RecordMigration.rollback_records()` deletes only migration-created records with
matching ETags/payload hashes, retains subsequent work, and never deletes the
financial ledger. Blob/Search artifacts are preserved until separately reviewed
cleanup; do not represent record rollback as complete artifact deletion.

## Remaining enterprise gates

Obtain separate authorization for real AI and microphone acceptance. Validate load, concurrency,
latency and recovery with real Azure providers. Connect authorized organization
sources; define tenant onboarding, role assignment, retention/deletion and source
approval policies. Free F1/Search/F0 limits provide no enterprise availability SLA.
Private endpoints/paid hosting/capacity changes need an approved cost plan.

Official sources: [Cosmos free tier](https://learn.microsoft.com/en-us/azure/cosmos-db/free-tier),
[Search RBAC](https://learn.microsoft.com/en-us/azure/search/search-security-rbac),
[semantic free billing](https://learn.microsoft.com/en-us/azure/search/semantic-how-to-enable-disable),
[layout capabilities](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout?view=doc-intel-4.0.0),
[document limits](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/service-limits?view=doc-intel-4.0.0),
[Python hosting](https://learn.microsoft.com/en-us/azure/app-service/quickstart-python),
[Consumption billing](https://learn.microsoft.com/en-us/azure/container-apps/billing),
[managed identity](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity),
[App Service pricing](https://azure.microsoft.com/en-gb/pricing/details/app-service/linux/),
[Azure retail prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices).
