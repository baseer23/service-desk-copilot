> **Archived — superseded on 2026-10-14.** This document records an earlier deployment, migration or validation. Its Azure persistence, Voice Live, billing and startup instructions are not current. Do not execute its old provisioning or deployment commands. See [current architecture](ARCHITECTURE_SIMPLIFICATION.md), [setup](../README.md) and [Speech](SPEECH.md). Historical backups and financial records remain protected.

# Azure-native implementation and financial boundaries

Continue from repair checkpoint `2ce47a2`. Azure is the deployed source of truth;
existing SQLite/Chroma remain intact for local development and rollback.

Use one LangGraph supervisor hosted with FastAPI and lifespan-owned stdio MCP.
Foundry Agent Service is not an additional supervisor. Voice Live already includes
speech recognition/rendering. Reuse the existing Foundry reasoning/embedding/voice
resource and monitoring instead of adding duplicate models or speech resources.

Target region: `eastus2`. Subscription:
`f4d123f6-dc20-4e1f-9d46-09ae395415f8`; existing group:
`rg-k_abdulbaseer-0925`.

| Component | Selection | Financial boundary |
| --- | --- | --- |
| Cases, conversations, checkpoints, jobs and shared usage policy | Cosmos DB NoSQL lifetime free tier; one region; one shared-throughput database, at most 1,000 RU/s | Free tier must be available and explicitly enabled. No autoscale or second region. Storage must remain below 25 GB. Fail instead of creating a paid replacement. |
| Knowledge | Azure AI Search Free; hybrid vector/text and free semantic billing plan | One free service per subscription; service/index/storage/free-query limits remain binding. No paid semantic plan or SKU upgrade. |
| Scanned/layout documents | Document Intelligence F0 | 500 pages/month and two analyzed pages per request; split PDFs into bounded page segments. No paid fallback. |
| Originals and extracted artifacts | Standard LRS Blob, private containers; Entra data-plane auth | Usage-based storage/transactions have small recurring costs. Obtain current CAD rates, reserve a conservative allowance and bound test data before provisioning. |
| Secrets | Key Vault Standard; managed identity | No API keys or connection strings. Store only the server signing secret; review transaction pricing. |
| Hosting | App Service Linux F1, Python 3.12, HTTPS, managed identity; Container Apps remains a supported deployment alternative | Current CAD retail rate for F1 is 0/hour. F1 has 60 CPU minutes/day and no production SLA/Always On. B1 is CAD 0.0241/hour (about 17.59/month at 730 hours) and exceeds the approved allowance. No paid host upgrade or registry. |
| Identity | Entra SPA/API registration plus managed identity and resource-scoped data roles | No client secret. Tenant and application audience validation on every data route; protected operator diagnostics. |
| Reasoning | Existing GPT-4.1 GlobalStandard deployment | Measure complete fictional contexts before choosing usable TPM. Higher throughput changes no monthly allowance and does not clear the emergency shutdown. |

Microsoft sources:
[Cosmos lifetime free tier](https://learn.microsoft.com/en-us/azure/cosmos-db/free-tier),
[Search Entra roles](https://learn.microsoft.com/en-us/azure/search/search-security-rbac),
[free semantic billing](https://learn.microsoft.com/en-us/azure/search/semantic-how-to-enable-disable),
[Document Intelligence limits](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/service-limits?view=doc-intel-4.0.0),
[Container Apps billing](https://learn.microsoft.com/en-us/azure/container-apps/billing).

Preserve the CAD 10 subscription alerts, CAD 8 estimated application allowance,
CAD 2 setup ceiling, complete existing ledger, daily voice limits and persistent
emergency shutdown. Budget alerts are not an invoice cap. No paid inference or
voice validation may bypass the shutdown. Separately authorized enablement is a
remaining live-validation gate.

Migration first creates private read-only snapshots and manifests, then copies
only explicitly approved fictional data. It never replaces or deletes local
stores. Checkpoints, source hashes, vector dimensions/provider provenance, case
facts/events and transcripts receive integrity checks. Rollback deletes only
unmodified migration-created records after an explicit operator action.

The public frontend shows investigations, documents, conversations, citations and
plain-language availability. Resource names, models, SKUs, MCP/LangGraph labels,
provider codes and financial diagnostics move to protected operator surfaces.

This is an implementation plan, not evidence of resource creation, deployment,
data migration or successful live Azure intelligence. Actual results are recorded
separately after validation.

## Reviewed CAD prices and bounded forecast

The public Azure Retail Prices API returned these `eastus2` CAD consumption rates
on 2026-10-13: General Block Blob v2 Hot LRS storage 0.0261/GB-month,
writes 0.0708/10,000 operations; Key Vault Standard operations 0.0425/10,000;
Linux F1 0/hour and Linux B1 0.0241/hour. The implementation bounds Blob artifacts
to 100 MB/1,000 objects and tenant state to 100 MB/5,000 records.

A conservative **CAD 0.25 monthly infrastructure forecast** is earmarked inside,
not added to, the existing CAD 8 application allowance before provisioning. It
does not clear the emergency flag, increase any allowance, or certify an invoice
cap. Existing unrelated monitoring charges remain outside the application's
control. Free-tier creation fails instead of falling back to paid SKUs.

Resource commands are generated by `scripts/provision_azure.py --plan` and are
restricted to this subscription/group/region and reviewed free/low-cost settings.
No PostgreSQL, paid Search, dedicated throughput, private endpoint, paid hosting
upgrade or extra agent/speech supervisor is automatically provisioned.
