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

## Official Neo4j MCP integration — 2026-10-18

DeskMate now launches the official `neo4j-mcp` 1.6.0 binary as an optional FastAPI-lifespan-owned stdio integration. Its dedicated adapter maps validated settings to canonical `NEO4J_MCP_*` child variables, forces read-only mode, disables telemetry, and passes no unrelated application secrets. Discovery retains only explicitly granted tools with read-only annotations; production remains denied with the default empty tenant grant. Chroma, SQLite, immutable originals and extraction artifacts remain local.

Focused adapter/security tests passed. The complete backend suite passed (**328 passed, two optional tests skipped**), all **52 frontend tests** passed, the production frontend build passed, and `pip check` found no broken dependencies. DeskMate started through `make dev`; its frontend returned HTTP 200 and `/health` reported the built-in MCP ready while accurately marking Neo4j MCP unavailable and Aura disconnected. A live attempt reached the configured Aura service but authentication was rejected with the sanitized code `Neo.ClientError.Security.Unauthorized`; therefore live MCP discovery, schema retrieval and the two read queries are not claimed successful. No write query, migration, backfill, ingestion, Azure inference or document upload was performed.

## Intelligent source routing — 2026-10-18

The knowledge and service-desk subgraphs no longer run unconditional local document/graph prefetches. Only the validated `InvestigationPlan.tool_calls` execute, while legacy checkpoint `prefetch_results` remain readable but inert. Plans now record selected and skipped sources as observable activity. Explicitly selected document results receive one provider-selected batch relevance grade; unknown, duplicated or incomplete grader IDs fail closed and rejected chunks never reach reasoning or citations. Keyword match strength and vector distance retain distinct relevance semantics.

Executed checks: **331 backend tests passed with two optional tests skipped; all 53 frontend tests passed; the production frontend build and changed-code undefined-name/syntax lint passed.** Tests verify zero document calls for a direct connected-database schema plan, batch rejection of an irrelevant immigration passage, legacy checkpoint compatibility, parallel execution, MCP permissions and removal of completed-state placeholder copy. No live model, Azure inference, public-web request or cloud mutation was performed.

## Public web and news research — 2026-10-19

Broad knowledge requests now distinguish general conversation, private-document retrieval and public research. Current-news requests use the read-only `search_news` MCP capability with Canadian-English results, a bounded day/week/month window and preserved publication metadata; ordinary public research continues to use `search_web`. A separate read-only timezone capability supplies current calendar context. Public search snippets remain discovery inputs, selected pages are fetched and evaluated before supporting an answer, and this request-scoped evidence is never inserted into Chroma or Neo4j. Follow-up classification retains the bounded transient conversation, while multi-headline answers are no longer collapsed to the single-line conclusion limit. Tool failures produce an explicit limitation instead of an invented result.

Executed checks: **336 backend tests passed with two optional Aura tests skipped; all 53 frontend tests passed; all 19 Playwright tests passed; the production frontend build, dependency check and changed-code undefined-name/syntax lint passed.** The deterministic route regression verifies one news search, selected page fetches, dates, publishers and links, zero local document/graph retrieval, and an answer beyond the former 280-character bound. After `make dev` restart, `/health` reported local MCP ready and Groq selected. A genuine Alberta request reached live news providers successfully, but the complete `/ask` call did not return within the bounded 180 seconds after search; no automatic retry was made because another provider request could be billable. End-to-end live answer quality therefore remains an explicit blocker, while live public-search reachability is established.

## Intelligent stopping and structured knowledge — 2026-10-19

Each evidence pass now ends in one structured outcome: answer, one material question or an explicit inability to verify. Verification is terminal for the current pass; only a new human answer begins another source-selection pass. One bounded policy/schema correction remains possible without re-querying sources. Verified answers are no longer discarded solely because the cycle counter reached its limit, while rejected claims retain their uncertainty and do not trigger an identical search.

Document upload now persists `standard` or `structured_graph` mode at creation, including across deduplication and retries. Standard mode retains the existing extraction/chunking path. Structured mode preserves page text/Markdown, spans, paragraphs, heading hierarchy, table dimensions/cell spans, figures/captions/positions and locally stored figure artifacts when Azure supplies them. Stable tenant-local objects are embedded in Chroma. Neo4j receives only 160-character reference descriptions and scoped IDs through separately configured opt-in writer credentials; raw text, table values, images, embeddings, local paths and secrets are excluded. Missing writer authorization leaves the successful local index in `graph_pending`.

Hybrid structured retrieval validates a bounded `GraphLookupPlan`, performs tenant-filtered reference lookup first, expands only approved relationships up to two hops, hydrates IDs from tenant-owned local Chroma content, semantically ranks that content locally and supplements with ordinary vector results. An empty or unavailable graph retains vector-only retrieval with an explicit enrichment status. General conversation and public-news routes do not invoke this path.

Executed checks: **341 backend tests passed with two optional Aura tests skipped; all 54 frontend tests passed; the production frontend build, dependency check and changed-code undefined-name/syntax lint passed.** No live provider, Azure extraction or Neo4j ingestion call was made, and no production document or graph data was modified.

## Observable-investigation verification — 2026-10-14

The existing graph now retains the plan's model-generated `unknowns` as visible research questions and streams a bounded public activity view containing the objective, research questions, MCP query, concise result labels, evidence gaps and next intended action. Full tool payloads and model reasoning remain outside the activity stream. The persisted plan and research questions render again with restored case state; LangGraph interrupts continue to resume the same `thread_id`.

Executed checks for this revision: **338 backend tests passed and two opt-in Aura tests skipped; 51 frontend unit tests passed; the production frontend build passed; `pip check` reported no broken requirements.** The Playwright run passed 18 of 19 checks and exposed ambiguous locators because next-action/evidence-gap values are now correctly visible in both live activity and assessment. The assertions were scoped to the assessment; the affected end-to-end scenario then passed on rerun. No live Groq, Azure OpenAI, public-web, external MCP or microphone request was made by these checks. Those remain live-environment acceptance blockers, along with validation against a newly entered random unfamiliar question.

## Document evidence integration — 2026-10-14

The existing Document Intelligence path remains additive to local extraction: native text and text-bearing PDFs stay local, while configured scanned PDFs, images, TIFF and Word documents use guarded `prebuilt-layout` analysis. Immutable originals, `extraction.json` artifacts and vectors remain in private local storage; page chunks and local SentenceTransformers embeddings remain in tenant-scoped Chroma. Extracted tables are reconstructed in row/column order before chunking. Every indexed chunk now carries its page, extraction method, document classification and per-page table count.

MCP `search_knowledge` remains the investigation read boundary. Its streamed completion event now includes bounded document titles, authenticated source paths, page references and extraction method. React shows those details in live investigation activity, restored evidence cards and direct knowledge search without exposing credentials or storage paths. Azure authorization, provider quotas and the rule against automatically retrying ambiguous paid analysis remain unchanged; document output is retained locally only.

Executed checks: **339 backend tests passed and two opt-in Aura tests skipped; 51 frontend tests passed; the production frontend build passed; two focused Playwright document upload/retrieval investigations passed.** No live Azure analysis request was made, so current Azure RBAC, service quota and real scanned-document behavior remain live blockers.

## Azure reasoning and local-only persistence — 2026-10-15

The Azure OpenAI deployment and configuration were verified without provisioning or mutation. DeskMate removed its historic CAD155 ledger, CAD90 forecast, request reservations, emergency spending switch and cost-based blocking. Provider errors preserve safe category-specific status through FastAPI and React, while Groq behavior and request-scoped provider selection remain unchanged. Document originals, extraction artifacts, vectors, conversations, cases and checkpoints remain local; Azure receives documents only for requested AI analysis and is not an application persistence backend.

The 2026-10-15 Voice repair keeps ordinary helpdesk conversation independent of MCP startup and routes `/voice-conversation` through Vite to FastAPI. One conversation identifier preserves bounded turn context, while the selected provider remains request-scoped. Real minimal Groq and Azure OpenAI turns succeeded through that browser-facing route; no provider fallback occurred.

On 2026-10-16, email collection became an explicit submode of that conversation. Canonical domains are loaded once from a validated local file and resolved only through MCP; compact expiring sessions own separate username/domain state, locks, history, version and request receipts. Deterministic parsing handles ordinary edits without model cost. Complex instructions use the selected provider only as a structured operation interpreter, after which Python validates and applies the edit.

The final migration replaces the email-specific runtime modules with reusable `conversation` and `resolution` packages. MCP exposes `list_registry`, `resolve_entity` and `reload_registry`; the canonical domain file moved to `backend/config/registries/domains.txt`, while the existing HTTP email routes remain compatibility contracts. The real stdio ownership chain remains FastAPI lifespan → tool harness → multi-client → MCP client → MCP server. Unknown valid domains remain usable after confirmation and test-only synthetic domains never enter the production registry.

Natural-language interpretation is now exclusively provider-backed. `conversation/interpreter.py` contains only context-aware safety validation, not transcript parsing. The operation schema distinguishes an explicitly complete value from a substring edit, preventing a model from overwriting an existing identifier with a fragment. Conversation state remains Python-owned, versioned and request-deduplicated; the model cannot write it directly.

## Demo-day minimal repair — 2026-10-19

Knowledge uploads now expose independent Azure DI, Neo4j read and Hybrid controls with defaults ON, ON and OFF. Azure DI runs only for an explicit upload, Neo4j availability reflects the discovered read-only tools, and standard uploads remain Chroma-first with no graph write. Public research skips empty evaluation, ignores model-invented URLs, excludes irrelevant or failed fetches without discarding earlier evidence, and uses one source-selection model call. Groq packing respects token capacity learned from provider headers.

Executed checks: **343 backend tests passed with two optional Aura skips; all 54 frontend tests passed; the production frontend build passed.** Live frontend/backend checks returned HTTP 200. Live Groq Express Entry and VPN requests returned HTTP 200; the VPN case stopped with one diagnostic question. A new explicit Azure DI synthetic-PDF job failed during extraction and remains `extract_inflight`; it was not retried because completion and billing are ambiguous. Existing Azure DI output remains locally indexed in Chroma. No Neo4j graph write or Azure OpenAI reasoning request was made.

## Streaming document uploads — 2026-10-19

HTTP document uploads now stream in one-megabyte chunks to a private random staging file, cross the MCP boundary by opaque token, and are SHA-256 hashed and atomically preserved from disk. PDF extraction has no application page ceiling. Azure DI PDFs are split into tier-aware page and byte batches, each completed paid result is saved locally before the next request, and embeddings and Chroma indexing remain local. No automated tests or live paid extraction were run for this requested change; startup and HTTP readiness were the only execution checks.

## Graph-first local hydration and recovery — 2026-10-19

Structured ingestion now creates tenant-scoped Document/Page/Section/Chunk/Table/Figure references while keeping full text, table values, vectors, images and originals out of Neo4j. Page containment and extraction-provided section membership are linked; page co-location alone no longer creates chunk-to-table or chunk-to-figure claims. Local Chroma metadata is indexed to a transactional SQLite object-reference map, so graph hydration performs bounded ID lookups instead of scanning a collection. Only hydrated local content is semantically ranked and allowed to support an answer.

An explicit graph-enrichment endpoint reuses saved extraction artifacts and byte-identical existing vectors, avoiding another Azure DI request. Local indexing survives graph failures as `graph_pending`, and retrying that state enters only the idempotent link stage. Model evidence-grading failure now retains retrieved documents for the ordinary reasoning/verifier assessment rather than converting valid retrieval into false absence.

Focused checks: 35 targeted document, graph, retrieval and MCP tests passed. Real stored `Telangana Today e Paper.pdf` evidence hydrated by stable object ID across pages 1–3. Real stored offer-letter evidence hydrated a page chunk and its separately indexed signature table, including the agreed start date. Both production documents are locally searchable and `graph_pending`; Aura read connectivity succeeded, but no ingestion credential is configured and the environment denied a rolled-back write-permission probe, so no production graph indexing is claimed. The requested live public-web fallback was also denied by the environment's external-access review and is not claimed.

## Recovery integration and MCP result boundary — 2026-10-20

The divergent local and GitHub histories were merged on a dedicated integration branch without resetting either side. MCP results are now normalized once at the LangChain-to-agent boundary, accepting canonical capability envelopes, raw object/result responses and raw lists while converting unsupported shapes into explicit error observations. Search rows remain discovery leads; model-selected URLs are fetched within a four-page budget, and only readable page bodies become usable public evidence. The article selector can choose up to four URLs, allowing a requested three-story answer without weakening source verification.

Focused checks passed for current-news hydration, malformed/error results, source-policy redirects, MCP fetch preservation, structured-output recovery and complete inventory context. `make dev` reported FastAPI/MCP ready on `127.0.0.1:8000` and React HTTP 200 on `127.0.0.1:5173`. The first real Alberta request executed live news search and webpage fetches but ended in an invalid Groq `InvestigationDecision`; a bounded same-provider recovery now falls from provider-side strict generation to JSON-object mode with local Pydantic validation. A subsequent inventory run completed that recovery but incorrectly asked the user for an inventory already returned by `list_documents`; inventory evidence is now preserved up to 30 rows and the reasoner is explicitly required to enumerate it while retaining valid document-disambiguation questions. The final rerun remained in the shared Groq capacity queue after classify, plan, reason and verify, so no end-to-end production-readiness claim is made. The official Neo4j MCP integration reported connected while the separate direct Aura enrichment flag remained false; supported vector-only behavior remained available. No stores, documents, Voice code, budgets or paid Azure processing were changed.

## Automatic Azure DI and reference-graph ingestion — 2026-10-21

PR #7 was integrated on a dedicated branch and completed as one upload architecture. When the explicit `AUTO_DOCUMENT_GRAPH_INGESTION` opt-in is enabled, supported PDF, DOCX and image uploads require writer preflight before Azure DI can start, use structured local indexing when saved, and publish reference-only `Document`, `Page`, `Section`, `Chunk`, `Paragraph`, `Table`, `TableCell` and `Figure` structure through the separately authorized Neo4j writer. Unsaved previews return extracted text to the caller, delete their staging file in the HTTP boundary, retain no local original/extraction/vector record, and keep only structural graph metadata. UTF-8 text/Markdown uses an identified local fallback rather than falsely claiming Azure processing.

Graph identifiers include document hash, extraction version, element type and source position. Neo4j receives bounded IDs, roles, spans, table coordinates, content hashes and serialized bounding coordinates, but no source text, cell values, images, embeddings, paths or credentials. Results now report actual node/relationship counts; saved records retain separate local-index and graph-link status. Unhydrated references from intentionally unsaved previews remain discovery-only and instruct the reasoner to request the original when content is needed.

Executed non-billable checks: nine focused backend tests passed for saved structure, unsaved reference-only previews, local text fallback, deterministic versioned IDs, graph privacy, bounded relationship discovery, hybrid hydration, ambiguous paid-operation guards and automatic text upload routing. The production frontend build passed. No Azure DI request, Neo4j production write, fixture population, store deletion, migration or Voice change was performed. Automatic ingestion remains disabled by default and must not be enabled until the configured allowance, Azure readiness and writer authorization are explicitly verified.

## Observable investigation and verifier recovery — 2026-10-21

The investigation graph now rejects contradictory plans that require evidence but select no capability. Explicit local-document and public-web source requests constrain the planner without replacing its tool-selection judgment. A verifier with an unresolved supported-fact gap returns to the planner for one bounded, non-duplicate lookup instead of regenerating against unchanged evidence. Activity events distinguish retrieval records, search leads and tool errors and expose concise intent, plan, selection, observation, assessment, recovery, verification and outcome summaries without hidden reasoning.

Focused non-billable checks passed: 15 backend activity/source-scope tests, five Investigation Activity component tests and the production frontend build. The exact `From knowledge tell me what are the requirements of NSNP.` regression executed the real MCP `search_knowledge` boundary against isolated temporary Chroma data. Broader legacy suites still contain expectations for the intentionally removed one-pass verifier behavior and were not relabeled as passes. No production stores, external web request, paid provider request, graph write, spending control or Voice code changed.

## General intelligence capability experiment — 2026-10-21

The existing MCP/LangChain boundary now exposes deterministic Decimal arithmetic, unit conversion and `datetime`/`zoneinfo` operations plus read-only Open-Meteo weather/geocoding, Nominatim place search, OSRM driving routes, Frankfurter/ECB reference-rate conversion and Nager.Date public holidays. Public clients use fixed HTTPS endpoints, bounded inputs and timeouts; public-web authorization remains mandatory. Results preserve provider, timestamp/date, location/timezone, units and source identity. Failed signatures are retained so a planner cannot repeat an identical failed request indefinitely.

The 40-case frozen offline catalog improved capability readiness from 8/40 at baseline `c2b7e60` to 40/40; this measures whether a validated tool path exists, not live-model correctness. Deterministic and mocked factual tests cover hostile expressions, DST, date arithmetic, conversions, weather observation/forecast separation, no-alert claims, mapped-place limitations, traffic-free routing, dated FX and national/subdivision holiday scope. A bounded free live check succeeded for Open-Meteo, Open-Meteo geocoding, Nominatim, OSRM, Frankfurter and Nager.Date in 4.0 seconds total with zero model tokens and no paid API. GNews was rejected because no authorized key was available; the existing news search plus article-body verification remains. Weather alerts and cycling/walking/traffic routing remain disabled rather than inferred. No new dependency, production-data write, database migration, credential, allowance or Voice change was introduced.

## Persistent semantic memory and mandatory hybrid policy — 2026-10-22

Saved document ingestion now enforces `structured_graph` at the HTTP and MCP boundaries. The UI presents hybrid indexing as an always-on policy rather than a user-disableable option. Native local extraction and Chroma indexing remain available when the separately authorized Neo4j writer is unavailable; graph delivery stays explicit as `graph_pending`. Azure Document Intelligence remains independently selected and is not silently invoked by the hybrid policy.

Authenticated conversation memory recognizes only explicit first-person name assertions, records a bounded source excerpt and SHA-256 provenance in tenant/owner-scoped SQLite, and queues an idempotent semantic graph batch. Corrections retain and supersede the previous fact. Neo4j receives scoped `User`, `SemanticFact` and `ConversationSource` nodes with `HAS_FACT`, `SUPPORTED_BY` and `SUPERSEDES` links through separately configured ingestion credentials. Graph delivery has zero driver retries and at most three application attempts; local confirmed memory remains available with an honest pending status. Profile read/search and revocation capabilities derive identity from signed request authority, never model arguments. Unrelated document text cannot alter a personal profile.

Executed non-billable checks: 14 focused semantic-memory/graph tests, 31 related MCP/security/document-job tests and four Knowledge Panel tests passed; the production frontend build passed. No live Aura write, Azure Document Intelligence request, provider inference, public-web request, store deletion, allowance change or Voice change was performed. Live authenticated cross-conversation recall through a configured Aura writer remains an acceptance blocker.

## Persistent chat registry and personal-memory index — 2026-10-25

Ordinary chat threads now have a tenant/owner-scoped SQLite registry whose identifier is the existing LangGraph `thread_id`. The registry stores only discovery metadata; transcripts and continuation state remain in durable checkpoints. React stores only that active identifier, restores the checkpoint through `/conversations/{id}`, lists ordinary chats independently from Cases, and clears only the pointer when starting a new chat.

Explicit remember commands now produce validated identity, preference, project, employment, technology or generic-note facts without a model call. SQLite remains authoritative for confirmed, superseded and revoked facts and for source, vector and graph delivery jobs. A model-specific `personal_memory_local_*` Chroma collection embeds only normalized facts, checks tenant/owner authority against SQLite on every result and removes superseded/revoked vectors. Neo4j remains an optional idempotent projection with minimal provenance; an outage leaves local recall available and its graph job pending. Relevant facts are loaded into the separate LangGraph `personal_memory` field before reasoning.

Before the instruction to stop executing tests, focused checks passed for the 11 general-conversation scenarios, 15 React App scenarios and nine semantic-memory scenarios. The final backfill and graph-metadata edits were inspected but were not followed by another test run. No live model, public web, Azure service or Neo4j write was executed.

## Offline test profiles and change-aware selection — 2026-10-25

Default pytest, CI and Playwright configuration now declares the offline profile, forces local stubs and installs a fail-closed socket guard. Loopback and Unix sockets remain available for FastAPI, Vite and real MCP stdio; named or non-loopback destinations fail before a provider SDK can connect, even if local credentials exist. Live tests are separately marked and require two explicit authorization settings.

`make test-fast` maps the merge-base, staged, unstaged and untracked paths to conservative backend/UI checks and expands unknown backend contracts to the complete offline suite. `make test-regression` is the full offline PR gate. Seven generic browser status variants were consolidated into stronger retained VPN, MFA and printer scenarios, while a real-browser conversation-refresh check was added. The inventory and profile matrix are recorded in `docs/TEST_INVENTORY.md` and `docs/TESTING.md`.

Per user instruction, this testing refactor was not executed. No before/after duration is claimed; the next authorized regression should record pytest durations and Playwright reporting before further consolidation.

## Source-grounded document semantic graph — 2026-10-25

Structured uploads now retain the existing extraction, Chroma indexing and reference-only Neo4j projection, then optionally enter a separately configured semantic stage. Bounded page/section batches are interpreted through the selected structured-reasoning provider; cursors, section proposals, candidate snapshots and placement plans persist in tenant-scoped SQLite under a versioned pipeline key. Provider context redacts common secret assignments and treats all document text as untrusted data. The stage never repeats extraction or embeddings.

The writer uses flexible `Entity.kind` values and generic `RELATED_TO.predicate` relationships. Conservative model placement may reuse only tenant-scoped candidate IDs returned by discovery; trusted Python validates decisions and logical evidence IDs before parameterized writes. `GraphMention` and `GraphClaim` records connect entities and relationships to the canonical `Document` plus existing structured object IDs, while full text remains local and in Chroma. Personal conversation memory remains a separate graph domain. Semantic ingestion is disabled by default and requires both its explicit setting and the dedicated Neo4j writer.

Per user instruction, no tests, lint, build, paid provider call, Azure extraction, or live Neo4j write was executed for this change. Live provider extraction quality, Aura permissions, and end-to-end placement remain acceptance blockers until explicitly authorized.

## Workspace reset and visible investigation activity — 2026-10-25

The API now provides operator-only prepare/confirm reset routes with an expiring principal-bound token, exact `DELETE` confirmation, tenant write blocking, active-stream and ingestion cancellation, per-store reporting, and a retained content-free reset audit. SQLite records/checkpoints, tenant-owned Chroma rows, verified tenant document paths, and tenant-owned Neo4j nodes are deleted independently; uncertain legacy vectors are retained and reported rather than erased. Neo4j deletion uses the dedicated writer and bounded tenant-scoped batches. A successful reset clears in-process sessions and starts a fresh ingestion worker.

The chat UI adds the guarded reset modal and a responsive 60/40 conversation/activity layout. Live activity is grouped into six user-readable stages, retains the complete event log under Technical activity, and persists only allowlisted visible event fields for conversation restoration.

Executed non-billable checks: Python bytecode compilation, TypeScript no-emit type checking, and Git whitespace validation passed. No reset, test suite, live provider request, Chroma deletion, file deletion, or Aura operation was executed. Legacy vectors without reliable tenant ownership and workspace-owned backups/staged files without ownership metadata remain retained and must be reported rather than deleted.

## Continuous chat and durable URL ingestion — 2026-10-26

Chat now renders the checkpoint-backed message list as one chronological transcript, appending the current summary only when it is absent from saved messages. Follow-up submission appears immediately as a pending user turn, transcript scrolling follows new content only while the reader remains near the bottom, and desktop activity uses a stable 65/35 transcript/activity grid that stacks on mobile. The redundant Recent conversations panel was removed without changing conversation persistence or case architecture.

URL ingestion now requires the same public-web, local-MCP and private-store conditions reported by readiness. Crawled pages enter `LocalDocumentRepository`, preserving immutable local source/provenance, compatible Chroma vectors and reference-only Neo4j delivery; graph failure returns `graph_pending` without rolling back local knowledge. Crawler and MCP failures expose bounded safe error codes rather than internal exceptions.

Focused checks passed: four URL-ingestion backend tests, 19 App/Knowledge frontend tests, Python compilation, TypeScript no-emit checking, Git whitespace validation and a live headless browser layout/menu check. A single bounded live `example.com` ingestion returned HTTP 400 and a read-only search confirmed no item was persisted; it was not retried because the mutation result must not be assumed safe to repeat. Runtime health and frontend returned HTTP 200. Aura enrichment was unavailable, and no graph write, reset, paid model call or full test suite was performed.

## Existing document-ingestion activation — 2026-10-26

The private local profile now enables the existing persistence, URL/public-web, structural Neo4j and semantic document-graph switches. Previously linked documents whose semantic status was durably recorded as `disabled` are moved to `pending` when structural linking next succeeds under an enabled semantic configuration, allowing the existing worker to discover them without changing the ingestion architecture. The private `.env` remains ignored and no credential values are committed.

A minimal authorized check confirmed local Chroma retrieval, MCP readiness, Azure Document Intelligence readiness, Neo4j read connectivity and the dedicated structured writer preflight. Re-linking one existing authorized URL document completed with four structural nodes and three relationships. Its semantic proposal completed with 14 entities and one relationship, but graph placement was rejected by application validation and remains failed for operator review; the paid model call was not retried. Two older Azure extraction jobs remain `extract_inflight` and were not repeated because their billing outcome is ambiguous.

## Complete integration recovery — 2026-10-26

Explicit saved-knowledge scope is now an application-enforced LangGraph route even when the classifier proposes the service-desk workflow. The knowledge subgraph owns a dedicated retrieval node that runs the validated local Chroma search and one bounded graph read concurrently. When the authorized official Neo4j MCP read tool is discovered, the application supplies fixed parameterized tenant-scoped Cypher; otherwise the built-in tenant-scoped graph capability reports its real unavailable/empty result. The model never writes Cypher and cannot skip local retrieval for this scope.

The document worker now resumes local indexing, idempotent structural linking and already-safe semantic placement states independently. Structural graph failures persist bounded exponential backoff and a terminal marker after five attempts; ambiguous Azure extraction and embedding dispatch states are never automatically repeated. Reference descriptions use document/section/caption metadata without storing body or cell text in Aura. Malformed graph-placement decisions are conservatively converted to unresolved mentions rather than unsafe entity reuse or an additional model call.

Verification used temporary offline stores and mocked providers: 97 document, URL, graph, MCP-security and LangGraph tests passed; all 57 frontend tests and the production frontend build passed. The official Neo4j MCP adapter also passed a bounded read-only live discovery/schema/`RETURN 1`/node-count check. No Azure Document Intelligence request, LLM request, graph write, document reprocessing, workspace reset or destructive operation was performed.

## Event-driven document pipeline repair — 2026-10-27

Durable document ingestion now uses one application-lifetime event instead of a fixed ten-second scan. Upload and paste routes signal only after the original and metadata are saved; startup still recovers eligible jobs, each signal drains successive indexing, structural-link and semantic stages, and persisted graph deadlines wake the worker without repeating Azure extraction. Worker and manual indexing share a tenant/document-scoped in-process lock. This protects the supported single-process profile; a multi-process deployment would still require a distributed lease or job coordinator.

Saved-document classification now accepts the structured intent result for uploaded PDFs, titles and follow-ups. Its local retrieval is forced through the existing graph-first `HYBRID` path, with local Chroma fallback and no graph metadata promoted to factual evidence. Semantic failures are automatically resumable only when the selected provider explicitly marks them recoverable; model-call budget exhaustion is terminal `partial` and ambiguous graph/model operations are not retried.

Focused offline checks passed for worker recovery/wakeup/backoff, local document job transitions, logical graph-to-Chroma reference hydration, conservative semantic placement, graph repository writes, saved-knowledge routing, the five Knowledge Panel scenarios and the production frontend build. No Azure extraction, paid reasoning request or live Neo4j write was executed; those remain the live boundary.

## Live document failure recovery — 2026-10-27

The live `NSNP 200.pdf` failure was an `AttributeError` in `AzureDocumentExtractor._regions`: the installed Azure SDK returned a flat numeric polygon while the serializer expected point objects. Azure had completed analysis, but the local exception occurred before the first immutable batch was saved. The serializer now accepts numeric, mapping and point-object polygon forms. Processing failures persist a safe stage, code, reason and source exception type, while logs include document ID and stack-frame-only traceback without SDK response bodies or document text.

Azure readiness now uses an authenticated read-only resource-info request; Neo4j writer readiness distinguishes missing settings, connectivity, authorization and schema/transaction validation. Reprocess signals the event-driven worker, resumable semantic states are eligible after restart, successful recovery clears stale failure fields, and the UI renders the persisted safe reason.

One focused polygon regression passed. One live operator-reviewed recovery completed: 14 Azure-extracted pages in seven immutable batches, 25 Chroma chunks, four tables, 412 structural graph nodes, 485 structural relationships and a linked semantic result with 14 entities/mentions and zero source-supported semantic relationships. Both Azure and Neo4j live readiness probes passed.

## Investigation validation recovery — 2026-10-26

Plan rejection now retains a safe structured diagnostic containing request ID, selected provider, stage, validation code, capability name, expected and actual argument names, and attempt number. Discovered MCP schemas remain authoritative. A rejected plan receives one bounded correction with exact schema feedback; authenticated identity questions that still fail correction use the existing owner-scoped `get_authenticated_profile` capability with its exact zero-argument schema rather than failing or inventing identity. Other invalid plans remain rejected.

Reasoning still rejects nonexistent evidence IDs. Its one correction now names the invalid and permitted IDs; a second failure strips unsupported references and terminates as an explicit cannot-verify result. Failed chat follow-ups restore the unsent text, prior messages and normal submit state.

The single live Groq reproduction classified `whats my name` as knowledge/general, reached `plan`, performed provider structured recovery, and then failed application plan validation twice before any MCP tool executed. The prior code discarded the validator exception, so historical logs cannot recover the exact rejected argument set. New deterministic coverage reproduced the likely profile-contract failure (`query` supplied to zero-argument `get_authenticated_profile`) and verifies its safe correction. Focused results: 25 general-conversation/memory/validation checks, five dedicated validation checks, one MCP execution check and 16 App checks passed; compilation, TypeScript checking and live health checks passed. No workspace reset, database mutation outside disposable test stores, or second live-provider request was performed.

## Graph-first saved-document retrieval — 2026-10-27

Saved-document titles now resolve on whole-token boundaries against the tenant's complete title inventory, which `list_available_context` returns once per request after the scope check and the agent keeps out of model context. A single plain-word title needs an explicit file reference, document families resolve to their newest version, and an ambiguous title is asked about once; an answer that still names no single document searches all candidates rather than asking again. Listing failure continues without title resolution.

Saved-knowledge retrieval is graph-first: one application-written, tenant- and document-scoped `read-cypher` call (independent pattern comprehensions, no `OPTIONAL MATCH` row multiplication), hydration of its object references through the application-only `resolve_graph_references` MCP tool, then local search as supplement or fallback. `graph_status` records `available`, `empty`, `stale`, `unavailable` or `error`; an MCP failure is never reported as an empty graph. Answer page citations come only from evidence IDs the model referenced. neo4j-mcp 1.6.x reads `NEO4J_MCP_*`; plain `NEO4J_*` names are deprecated aliases and are not set for the child process. The document-graph e2e acceptance test is now `live`-marked and skips unless both live flags are set; `pytest.ini` was removed so `pyproject.toml` registers every marker.

Executed offline checks: 28 new graph-first tests (mocked neo4j-mcp text/JSON results, a real stdio server for row, empty-list and error results, temporary Chroma/SQLite) and the focused MCP, retrieval, general-conversation, supervisor and scope suites: 88 passed, 6 failed. All six failures predate this change: `search_semantic_memory` runs before the scope check (four scope cases), and two supervisor cases are covered in the branch report. No running application, Azure, Groq or Neo4j Aura request was made. Remaining live blockers: a real chat answer over an existing saved document through the configured Aura instance, and the exact live `read-cypher` response envelope from neo4j-mcp 1.6.0.

## Whole-site URL ingestion — 2026-10-28

"Add from URL" now starts a durable crawl job instead of crawling, indexing and linking inside one MCP call. `ingest_url` stores a `url_crawl_job` record and returns its ID; a FastAPI `url-crawl-worker` drives the application-only `advance_url_crawl` MCP tool in bounded steps (at least one page, at most 20 seconds), persisting queue, seen URLs and robots rules after each page so jobs resume after restart. Seeds come from robots `Sitemap:` lines and `/sitemap.xml` (defusedxml, same-origin, robots-allowed). Limits are per page (`url_max_page_chars`) plus an overall safety cap; robots, same-origin, rate limiting and public-URL validation are unchanged. Pages are saved as `text/html` originals with URL provenance and a canonical-URL document family, and the existing document worker indexes and links them; `html_extraction` gives them the same Section/Paragraph/Table/TableCell/Chunk structure as PDFs and never reaches Azure. Neo4j Documents carry `source_type`, `source_url` and `canonical_url`; `(:Website)-[:HAS_DOCUMENT]->(:Document)` and `(:Document)-[:LINKS_TO]->(:Document)` are written once per page. Answers cite web pages by title and URL. `GET /ingest/url/{job_id}/status` drives the URL tab's progress display.

Executed offline checks: 7 new crawl-job tests and 2 frontend crawl-summary tests; with `test_ingest_url`, `test_local_document_jobs`, `test_document_worker` and `test_graph_first_documents`, 54 passed and 3 failed — the three `test_ingest_url` tests that assert the former synchronous response (`pages`, `chunks`, immediate linking) and were deliberately left unchanged. Local documents, web research, MCP, MCP security, graph repository and multi-MCP suites: 73 passed. Frontend: 58 passed, 3 pre-existing `ProviderSelector` failures; production build passed. Remaining live blockers: a real crawl, the Website/LINKS_TO Cypher against Aura, and semantic ingestion of URL documents (disabled).

## Website ingestion hardening — 2026-10-29

A read-only diagnostic (4 GETs with the crawler's own session) showed the crawler was sending `python-requests/2.32.3`: `session.headers.setdefault("User-Agent", ...)` never applied because `requests.Session` already has one. The site's own nginx answered 403 (no `cf-mitigated` challenge header), its robots.txt asked for `Crawl-delay: 10`, and its sitemap index (on the apex) listed only www URLs while the crawl origin was the apex.

The crawler now identifies itself as `DeskMateBot/1.0 (+URL_CRAWLER_CONTACT)` with standard Accept headers, honours robots Crawl-delay, resolves the apex/www origin from one root fetch before reading robots and sitemaps, rewrites same-site alias URLs to the canonical host, never keeps bot-protection cookies, stops a job as `blocked_by_site` on a challenge or three consecutive 403s (keeping saved pages), and waits out a bounded Retry-After on 429. Saved pages are content-addressed after removing scripts, comments and nonces. Optional Azure Document Intelligence layout analysis of saved pages (`URL_AZURE_ANALYSIS=false` by default) is merged with the local parse's links, canonical URL and tables. The document worker no longer lets a document whose pass changes nothing starve later queued pages, LINKS_TO counts report relationships actually created, and the crawl status reports crawl stages and per-page ingestion stages separately.

Offline: 13 new mocked tests and the existing crawl, worker, local job and graph-first suites pass; the full offline suite has the same 11 pre-existing failures. Not yet verified live: a crawl with the new identity, Azure page-unit cost, Aura Website/HAS_DOCUMENT/LINKS_TO counts and a chat answer from crawled pages.
