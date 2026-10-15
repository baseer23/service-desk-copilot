# Architecture simplification — 2026-10-14

This document describes implemented behavior. Actual live results, Azure resource deletion receipts and final test/commit evidence are recorded separately below when executed; configuration alone is not live acceptance.

## Current architecture

```text
React chat / editable completed Speech transcript
  → FastAPI authenticated requests or SSE
  → request-scoped Groq (default) / Azure OpenAI selection
  → compiled LangGraph supervisor and investigation subgraphs
  → validated LangChain tools
  → built-in MCP stdio plus explicitly configured MCP servers
  → local Chroma / local transactional SQLite / private original files
  → optional Neo4j Aura relationships
```

Azure AI Speech SDK continuously transcribes microphone PCM and returns partial/final text. It does not generate responses or speech. Its provider is independent of the model selector. Azure Document Intelligence can extract supported document content, while originals/extraction artifacts remain local. Local SentenceTransformers generates semantic embeddings.

Removed active components include Azure Voice Live/GPT-Realtime, speech synthesis, Azure-hosted application persistence adapters, Cosmos/Blob/Search dependencies and deployment paths that provision them. Old case/transcript/checkpoint/usage formats remain readable locally. Historical reviews are visibly archived and must not be used as setup or provisioning instructions.

## State and preservation

| Data | Active location | Protection |
|---|---|---|
| Original PDFs and uploaded files | `LOCAL_DOCUMENTS_DIR` | Immutable names/hash verification, private directory |
| Extracted text/artifacts | Local document directory | Original retained; exact SHA-256 deduplication |
| Semantic knowledge | `CHROMA_DIR` | Persistent, model/provenance-specific collections |
| Cases, facts, conversations, jobs | `CASE_DB_PATH` local SQLite | Tenant/owner scope and transactions |
| Graph relationships | Configured Neo4j Aura | Explicit provenance, tenant scope, optional availability |
| LangGraph checkpoints | Local SQLite record namespace | Stable thread IDs and imported serialization |
| Historical and new Azure estimates | Existing local policy ledger | Approved cap/emergency/history preserved |
| Groq token counters | Private local numeric usage ledger | No prompt, response text, email or credential storage |
| Backups | `store/backups/` | Private and ignored; do not delete unverified sources |

Verified migration precedes source deletion. Local re-embedding never mixes earlier Azure or stub vectors with SentenceTransformers vectors. Lexical archive collections preserve earlier text/provenance. A database error never triggers collection reset. Production fixture data is excluded from live retrieval; archived fixtures remain available only for explicit isolated tests.

## Reasoning and MCP

The graph implements understanding, analysis, entity identification, memory lookup, dynamic MCP discovery/planning, local knowledge/graph retrieval, validated execution, observation, candidate reasoning, independent verification, bounded reflection, human interruption and finalization. The concrete nodes and conditional edges are in `backend/app/agent/graph.py`. `MAX_INVESTIGATION_CYCLES` and `MAX_RESEARCH_ROUNDS` bound loops. Missing or contradictory evidence results in uncertainty, further authorized research or a necessary question rather than a fabricated answer.

MCP schemas and descriptions drive tool availability. Additional server descriptors are namespaced and independently fail unavailable; future service-desk platforms are not represented as connected unless a real connection exists. Model plans are restricted to approved reads. Tool invocation results and actual execution statuses support the activity panel. Cancellation stops local graph work; it cannot guarantee cancellation of a remote request already accepted by a service.

## Model switching and Groq throttling

The browser persists `deskmate.reasoning-provider` and sends `X-DeskMate-Provider` on subsequent API/SSE requests. A context variable captures the selection for the entire request and nested graph tasks. The shared LangChain Runnable facade delegates structured calls to the selected adapter. No Groq failure switches to Azure. Missing credentials and throttle failures return clear selected-provider errors.

A centralized model-specific Groq limiter serializes unknown concurrency, queues when capacity is unavailable, estimates input/output tokens and records returned numeric usage. It honors provider reset headers and Retry-After. Groq request headers describe **daily** requests, token headers **minute** tokens; optional exact RPM/TPD settings come from the account. It retries only explicitly rejected 429 model calls, with bounded exponential backoff/jitter. Tool execution is outside the retry loop. Timeouts and ambiguous transport failures are not automatically retried. Independent local retrieval can continue during model capacity waits with real stage events.

The default model is `openai/gpt-oss-120b`; configured models remain explicit. Groq retired the older Llama 3.3 free/developer model on 2026-08-16; enterprise contracts differ. GPT-OSS requests use low reasoning effort and exclude internal reasoning from responses. Current SDK URL serialization is regression-tested because a mocked response alone would not detect a doubled API path. Request storage is disabled where supported. Prices remain unknown until configured from the applicable account; unknown does not mean free.

Primary references: [Groq rate limits](https://console.groq.com/docs/rate-limits), [model deprecations](https://console.groq.com/docs/deprecations), [Groq reasoning parameters](https://console.groq.com/docs/reasoning), [official SDK retries](https://github.com/groq/groq-python#retries).

## Speech and safety

See [Speech](SPEECH.md) for the current state machine and configuration. Ready to Talk starts continuous recognition. Partials replace unfinished text; finals accumulate without duplicate reconnect fragments. Sentence boundaries/silence do not end recognition. A standalone Stop command is excluded from the transcript and releases resources. Replies are text only. A completed transcript enters the same provider-selected LangGraph flow.

Connected Speech time includes silence and mute. DeskMate records connected duration locally for transcript diagnostics, but does not reserve money, impose an application allowance, or block use based on estimated provider cost. The Azure service's authentication, quota and rate-limit responses remain authoritative.

## Configuration and restart

Review `.env.example`; `.env.azure.example` is an optional AI-processing overlay for the same local profile. Neither provisions cloud persistence. Never overwrite an existing `.env`, signing key or local stores. Groq keys, Azure tokens and Aura credentials remain backend-only. `VOICE_*` identity names retained in the configuration preserve existing cookie/history ownership; they do not re-enable Voice Live.

Run `make dev` and open `http://127.0.0.1:5173`. FastAPI owns MCP. Python code/prompt or backend environment changes require restart. Provider switching applies to subsequent requests without restart. Keep local stores and verified backups at their existing private paths.

## Verification record

- Deterministic ten-scenario graph results and their exact scope: [REASONING_EVALUATION.md](REASONING_EVALUATION.md).
- Live evaluator: `scripts/evaluate_reasoning.py --provider groq --output PRIVATE_PATH --live`. It uses fresh empty local stores, no fixtures, no private Aura data, no external MCP servers and no public web. Azure evaluation uses the selected Azure provider and records actual stages, tools, evidence, token usage and duration without attempting to infer billing.
- Live speech evaluator and physical microphone acceptance: [SPEECH.md](SPEECH.md). A prerecorded SDK pass is not a human microphone pass.
- Actual cloud removal, final persistence restart results, exact test counts and final Git SHA must be appended from executed checks. Earlier review results must not be reused as evidence for this revision.


## Executed migration and live acceptance

On 2026-10-14 the complete 1,967-record Cosmos export matched the cloud source. All 27 Blob objects were downloaded and their hashes matched the verified backup; all seven Search chunks matched exactly. Locally, 1,965 business payloads matched exactly, and one changed owner pointer referenced a later retained conversation while both old/new conversations remained present. The financial policy retained its authorized local history. A fresh 46-file local snapshot verified before deletion.

The three retired DeskMate resources were deleted in subscription `f4d123f6-dc20-4e1f-9d46-09ae395415f8`, group `rg-k_abdulbaseer-0925`:

| Resource | Region | Verified final state |
|---|---|---|
| `deskmate-f4d123f6-cosmos` | East US 2 | Deleted; absent from final resource inventory |
| `deskmate-f4d123f6-search` | Central US | Deleted; absent from final resource inventory |
| `dskf4d123f6store` | East US 2 | Deleted; absent from final resource inventory |

There is no remaining provisioned database/storage account for these resources, so their future provisioning charges stop. Existing invoice charges and Azure billing-report delays are not erased, and no zero-total-Azure-bill claim is made. Local verified backups remain private under `store/backups/azure-local-20261014-verified`, `store/backups/local-20261014-after-voice` and `store/backups/local-20261014-speech-vector-cutover`.

SentenceTransformers reindexing verified five actual retained chunks in its separate 384-dimensional local collection. Original collections were neither reset nor deleted. Explicit fixture material remains archived and excluded from live retrieval. Aura was deferred by the user; vector-only retrieval is the active mode.

Real Azure Speech SDK recognition passed using prerecorded audio and the existing compatible AIServices S0 resource, without provisioning another resource or retrieving keys. The actual React microphone pipeline passed with a simulated prerecorded microphone: final transcription, mute/unmute, Stop, restart availability and zero browser exceptions. A separate real WebSocket run recognized two consecutive phrases followed by spoken Stop, excluded that command, and closed normally (13 partial events, two final transcript events, 12.324 seconds). Physical microphone access was not used. The Stop sample came from Google's CC-BY [Speech Commands dataset](https://www.tensorflow.org/tutorials/audio/simple_audio), downloaded privately and never ingested as production knowledge.

Live testing found and fixed two adapter-contract defects that pure mocks missed: the installed Speech SDK exposes `disconnect_all()` rather than `disconnect(callback)`, and the installed Groq SDK already includes the OpenAI route prefix. Actual-SDK regression checks now cover both. A cancelled per-request MCP response pipe also no longer closes the shared session when a late response arrives. Availability metadata excludes disconnected fixture sources from model plans.

Groq's ten live missing-evidence scenarios and their limits are in [REASONING_EVALUATION.md](REASONING_EVALUATION.md). Azure OpenAI is selected explicitly and uses its real authentication, service and quota response. DeskMate has no financial ledger, spending allowance or recognition reservation.

Final executed checks: **323 backend tests passed, two Aura integration tests skipped by explicit policy; 49 frontend tests passed; 19 Playwright tests passed; production build and pip dependency checks passed.** Python undefined-name/unused-code/syntax lint (`ruff --select F,E9`) passes. Comprehensive style lint is not clean: it still reports line-length, formatting and style-rule findings in changed and pre-existing code; no broad formatter was run. This is separate from the passing functional/type/build checks.

After restarting the final local stack, all 2,119 pre-existing non-financial business records exactly matched their verified snapshot. Original/extraction artifact hashes and every Chroma collection count matched before/after restart. A new non-sensitive Groq greeting added its own real conversation checkpoints; existing records were not replaced. The actual UI rendered the real Groq response and streamed operational events, with the selected-provider header verified. Speech readiness and the Start control were available. Production history excludes explicitly labeled fixtures, and Saved Documents remains absent.

The final `make dev` process leaves React on `127.0.0.1:5173`, FastAPI on `127.0.0.1:8000`, and its owned MCP subprocess running. CLI sign-in and local credentials remain private. Do not run a second MCP server alongside the API.

## Observable-investigation verification — 2026-10-14

The existing graph now retains the plan's model-generated `unknowns` as visible research questions and streams a bounded public activity view containing the objective, research questions, MCP query, concise result labels, evidence gaps and next intended action. Full tool payloads and model reasoning remain outside the activity stream. The persisted plan and research questions render again with restored case state; LangGraph interrupts continue to resume the same `thread_id`.

Executed checks for this revision: **338 backend tests passed and two opt-in Aura tests skipped; 51 frontend unit tests passed; the production frontend build passed; `pip check` reported no broken requirements.** The Playwright run passed 18 of 19 checks and exposed ambiguous locators because next-action/evidence-gap values are now correctly visible in both live activity and assessment. The assertions were scoped to the assessment; the affected end-to-end scenario then passed on rerun. No live Groq, Azure OpenAI, public-web, external MCP or microphone request was made by these checks. Those remain live-environment acceptance blockers, along with validation against a newly entered random unfamiliar question.

## Document evidence integration — 2026-10-14

The existing Document Intelligence path remains additive to local extraction: native text and text-bearing PDFs stay local, while configured scanned PDFs, images, TIFF and Word documents use guarded `prebuilt-layout` analysis. Immutable originals, `extraction.json` artifacts and vectors remain in private local storage; page chunks and local SentenceTransformers embeddings remain in tenant-scoped Chroma. Extracted tables are reconstructed in row/column order before chunking. Every indexed chunk now carries its page, extraction method, document classification and per-page table count.

MCP `search_knowledge` remains the investigation read boundary. Its streamed completion event now includes bounded document titles, authenticated source paths, page references and extraction method. React shows those details in live investigation activity, restored evidence cards and direct knowledge search without exposing credentials or storage paths. Azure authorization, provider quotas and the rule against automatically retrying ambiguous paid analysis remain unchanged; document output is retained locally only.

Executed checks: **339 backend tests passed and two opt-in Aura tests skipped; 51 frontend tests passed; the production frontend build passed; two focused Playwright document upload/retrieval investigations passed.** No live Azure analysis request was made, so current Azure RBAC, service quota and real scanned-document behavior remain live blockers.

## Azure reasoning and local-only persistence — 2026-10-15

The Azure OpenAI deployment and configuration were verified without provisioning or mutation. DeskMate removed its historic CAD155 ledger, CAD90 forecast, request reservations, emergency spending switch and cost-based blocking. Provider errors preserve safe category-specific status through FastAPI and React, while Groq behavior and request-scoped provider selection remain unchanged. Document originals, extraction artifacts, vectors, conversations, cases and checkpoints remain local; Azure receives documents only for requested AI analysis and is not an application persistence backend.
