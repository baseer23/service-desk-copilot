# Reasoning evaluation — 2026-10-14

These ten scenarios ran through actual LangGraph nodes and conditional edges, LangChain StructuredTool validation, MCP SDK stdio, temporary SQLite persistence and temporary persistent Chroma. Structured model responses were explicit deterministic fixtures. External LLM inference was not executed by this suite, so this demonstrates orchestration and evidence safeguards rather than Groq or Azure model quality. Test knowledge remained in isolated pytest directories.

| Scenario | Outcome | Status | Evidence items | Reasoning iterations | Duration |
|---|---|---|---:|---:|---:|
| Microsoft 365 MFA authentication failure | Passed | `assessment_ready` | 4 | 1 | 0.228 s |
| VPN authentication failure | Passed | `assessment_ready` | 9 | 3 | 0.459 s |
| Network printer unavailable | Passed | `assessment_ready` | 4 | 1 | 0.166 s |
| Active Directory account lockout | Passed | `assessment_ready` | 5 | 1 | 0.197 s |
| Outlook connectivity failure | Passed | `assessment_ready` | 5 | 2 | 0.339 s |
| Multiple users reporting internet outages | Passed | `assessment_ready` | 2 | 1 | 0.231 s |
| Application access denied | Passed | `assessment_ready` | 4 | 1 | 0.241 s |
| Corporate Wi-Fi connection failure | Passed | `assessment_ready` | 2 | 1 | 0.162 s |
| Contradictory knowledge-base troubleshooting instructions | Passed | `assessment_ready` | 4 | 2 | 0.172 s |
| Incident with incomplete information and no matching knowledge | Passed | `needs_input` | 0 | 1 | 0.152 s |

Every scenario executed classification, ticket analysis, plan generation, evidence collection, reasoning and verification. Diagnostic calls went through dynamically discovered MCP schemas and LangChain tools. Recommendations reference retrieved evidence IDs; no case was declared resolved. The incomplete incident returned a real LangGraph human-input interrupt with zero confidence and no invented diagnosis.

The contradictory-KB scenario supplied two different stored articles. Its verifier rejected an unsupported certificate-validation recommendation, triggered another reasoning pass and retained the contradiction in the final result. The resulting recommendation asks the policy owner to reconcile the sources while preserving certificate validation.

Corporate Wi-Fi explicitly considered graph relationships. No live Neo4j availability is claimed by this suite; vector-only fallback is tested separately.

Per-scenario model token usage and estimated inference cost are **unavailable**, because the model is a fixture. No paid inference requests ran. The detailed runtime report records actual stages, MCP calls, evidence references, rejected claims, durations and null token/cost fields.

Reproduce with:

```bash
REASONING_MATRIX_REPORT=/private/tmp/deskmate-reasoning-evaluation.json \
  .venv/bin/python -m pytest backend/app/tests/test_architecture_reasoning_matrix.py -q
```

The runtime report contains isolated test identifiers and is created with private file permissions. Live provider, real microphone STT and actual Azure resource checks require separate verification and must not be inferred from these results.


## Live Groq verification

Real Groq inference also ran for all ten scenarios, using `openai/gpt-oss-120b`, actual LangGraph/MCP, fresh empty Chroma/SQLite, disabled fixtures, no private Aura and no external service-desk connections. Every final scenario requested necessary information. All ten passed the **limited missing-evidence/provenance checks**; this does not establish diagnostic accuracy against connected business systems.

| Scenario | Final state | Diagnostic evidence | Verifier unsupported claims | Tokens | Seconds |
|---|---|---:|---:|---:|---:|
| Microsoft 365 MFA authentication failure | `needs_input` | 0 | 0 | 11,651 | 48.289 |
| VPN authentication failure | `needs_input` | 0 | 0 | 10,348 | 90.367 |
| Network printer unavailable | `needs_input` | 0 | 0 | 11,197 | 72.543 |
| Active Directory account lockout | `needs_input` | 0 | 0 | 11,264 | 88.053 |
| Outlook connectivity failure | `needs_input` | 0 | 0 | 8,317 | 70.569 |
| Multiple users reporting internet outages | `needs_input` | 0 | 0 | 9,199 | 67.881 |
| Application access denied | `needs_input` | 0 | 0 | 10,213 | 90.476 |
| Corporate Wi-Fi connection failure | `needs_input` | 0 | 0 | 8,112 | 56.795 |
| Contradictory knowledge-base troubleshooting instructions | `needs_input` | 0 | 0 | 6,022 | 10.709 |
| Incomplete information with no matching knowledge | `needs_input` | 0 | 0 | 8,269 | 74.940 |

Each final run executed real planning, MCP discovery, retrieval, reasoning and verification, followed by a LangGraph human-input interrupt. Source absence was explicit. Unconnected organization tools returned unavailable, not business records. An evaluator metric initially counted these unavailable wrappers as fixture evidence; final metrics were recomputed from the exact preserved case states without further inference. Their contents were only availability notices. The initial reports remain privately preserved.

The first conflicting-KB run incorrectly used general chat and skipped retrieval. Classification instructions were corrected to route internal-KB comparisons through retrieval and verification, then that scenario was rerun live successfully. No source articles were injected into this live test; the deterministic test above separately verifies actual contradictory retrieved articles.

The final ten runs used **94,592 reported tokens** and **670.622 seconds** in aggregate. Provider capacity waits account for much of the duration. Groq pricing is unconfigured, so the API-cost estimate is unknown, not assumed zero. Initial failed requests and the initial incorrect routing run are excluded from these final-run totals and retained in private reports.

A tiny Azure OpenAI structured-text connectivity probe passed with Entra authentication. The full Azure investigation matrix was **not run**, because the unchanged CAD155 allowance has only a few cents remaining. No ledger reset or allowance increase was performed. Aura remains disabled at the user's request because the saved hostname no longer resolves. No physical microphone acceptance is claimed.

## Reliability-overhaul evaluation additions

The opt-in evaluator now contains seventeen prompts and explicit scores for understanding, clarification, tool selection, search quality, evidence quality, reasoning quality, citation accuracy, efficiency, resilience and explainability. The new set includes the exact `my vpn isnt working` regression, ambiguous and misspelled requests, a simple factual question, current and multi-source public-research questions, unavailable private facts and an incorrect premise.

The deterministic regression verifies that `my vpn isnt working` produces one material clarification with zero diagnostic confidence instead of broad web searching or a generic failure. Public-web integration tests run through the actual MCP stdio protocol with deterministic network adapters; fetched evidence remains request-scoped and source failures remain explicit. These tests do not claim live internet or model inference.

## Visible autonomous-investigation regression — 2026-10-14

The offline evaluation catalog now includes the Nova Scotia graduate-immigration eligibility prompt without encoding an eligibility answer. Classification guidance routes current eligibility, law, government-program and vendor-support questions through evidence research rather than unsupported general generation. Planning must produce concise research questions, and regression tests verify that the streamed event exposes those questions and its objective. A separate MCP regression verifies that the actual knowledge query and bounded result labels are emitted only after a successful capability result.

The complete offline backend suite passed (**338 passed, two Aura tests skipped**), including unfamiliar incidents, missing evidence and human interrupts, contradictory sources, explicit MCP failures, provider selection, and one-time Groq context-overflow compaction. All **51 frontend unit tests** passed and the production build succeeded. These deterministic checks do not establish the current immigration rules, live source reachability, live model behavior, or citation quality for an arbitrary future prompt. Those require an explicitly authorized live run with public-web access and the selected provider; no such paid or external request was made in this revision.

## Extracted-document evidence regression — 2026-10-14

Document evidence retains the existing local-first/Azure-layout pipeline and now exposes enough bounded provenance for the investigation UI to explain what was retrieved. A new repository regression passes a fictional image through the managed extraction contract, verifies table cells remain in row/column order, and verifies page, `document_intelligence` method, classification and table-count metadata reach the Chroma records. Activity regressions verify that MCP knowledge retrieval publishes the real query plus source title, authenticated source path and page reference without source text or hidden model reasoning.

The full offline backend suite passed (**339 passed, two optional Aura tests skipped**); all **51 frontend tests** and the production build passed. This does not claim a new live Azure Document Intelligence call. Live endpoint authorization, remaining F0 allowance and real OCR/layout quality were not exercised.

## Azure OpenAI diagnostics and visible conclusions — 2026-10-15

Azure Resource Manager metadata confirmed that `deskmate-reasoning` is a succeeded Global Standard `gpt-4.1` deployment and the configured endpoint, deployment and `2024-10-21` API version agree with the application configuration. The historic local allowance failure was removed: DeskMate now sends explicitly selected Azure requests without a local spending reservation, limit or silent provider change.

Entra credential failures, HTTP authorization/configuration/capacity errors, timeouts, context limits and invalid structured responses retain distinct diagnostics. Provider routing remains request-scoped and never silently changes Azure to Groq. Azure billing and provider quota controls, rather than a DeskMate ledger, govern paid usage.

LangGraph now emits actual understanding, planning, MCP query/result, evaluation, follow-up and conclusion data in execution order. Provider mechanics are retained under expandable diagnostics. Follow-up events precede the persistent interrupt and resume the same case checkpoint. Supported final answers are whitespace-normalized, capped to one short line and rendered in bold; evidence and sources remain in the timeline.

Executed verification is recorded with the applicable revision. The browser suite passed 18 scenarios, then exposed a checkpoint-resume stall in an attempted serialized-prefetch change; that change was reverted and the affected two-interrupt/resume scenario passed on focused rerun. Azure availability is determined by its real authentication, service and quota response.

## Voice helpdesk provider check — 2026-10-15

The repaired Voice path used the same conversation session for one minimal real Groq turn followed by one minimal real Azure OpenAI turn. Both providers returned relevant, context-consistent printer troubleshooting replies through the frontend proxy. The deterministic browser regression verifies greeting, stop-boundary transcript flush, exactly-once submission, rendered response and a subsequent recording turn; it does not claim physical microphone or new live Azure Speech recognition.

## Email correction interpreter check — 2026-10-16

Straightforward address capture, grouped spelling, substring changes, positional edits, undo and domain resolution bypass model inference. The exact reported six-turn sequence preserves the approved domain and finishes with the intended username. Azure OpenAI produced a valid clarification operation against a synthetic session. Groq returned invalid structured output for its one synthetic complex instruction; DeskMate rejected it without mutation or fallback and did not automatically retry it. This remains a live Groq structured-output limitation, not a deterministic correction failure.

## Generic conversation runtime verification — 2026-10-16

The exact five live-regression transcripts now pass consecutively: capture yields `abdulbasir@ngenuity.ca`, the local-only correction preserves the domain, a repeated full address remains clean, the spelled replacement yields `abdulbaseer@ngenuity.ca`, and confirmation resumes the remembered ticket request. Tests also cover insertion, deletion, replacement, undo, unknown-domain confirmation, ticket-reference correction, 1,000 independent sessions, general VPN routing and real MCP stdio discovery/execution.

The final full backend suite completed with **321 passed and two optional Aura skips**. All **52 frontend unit tests**, all **19 Playwright scenarios**, the production build and dependency check passed. The first concurrent browser/full-backend run exposed resource contention plus an ambiguous text locator; both scenarios passed in isolation, and the locator was scoped to the Voice transcript before the clean final browser rerun.

A bounded live Groq `openai/gpt-oss-120b` request used strict JSON Schema and applied a synthetic `qauser` → `qaanalyst` substring correction while preserving `ngenuity.ca`; no fallback occurred. The current runtime reports Azure OpenAI unconfigured because `AZURE_FOUNDRY_ENDPOINT` is absent, so no Azure model request was attempted. No physical microphone or fresh Azure Speech request was made.

## Model-first conversation evaluation — 2026-10-16

The six-turn ticket/email regression now invokes structured model interpretation on every turn. Tests assert that history, current entities, active task/entity and pending clarification reach the provider; substring-as-whole-field operations are rejected without mutation; repeated values are no-ops; and confirmation/undo intent must match its operation. General VPN and ticket-reference flows remain covered.

Final checks: **324 backend tests passed with two optional Aura skips; 52 frontend tests passed; 19 Playwright scenarios passed; production build, dependency check and changed-code lint passed.** Real Groq processed a synthetic ticket request, extracted `qatester@ngenuity.ca`, applied `tester` → `analyst` as `REPLACE`, and subsequently confirmed `qaanalyst@ngenuity.ca`. MCP resolved the domain and no provider fallback occurred. Azure OpenAI could not be exercised because the active environment reports it unconfigured; physical microphone and Azure Speech recognition were not exercised.

## Neo4j MCP evidence boundary — 2026-10-18

Knowledge and service-desk planners can receive the discovered `neo4j__get-schema` and `neo4j__read-cypher` schemas through the existing LangChain harness. Built-in `search_graph` and `search_knowledge` remain available. Allowlist, JSON Schema, read-only annotation and production tenant checks run before invocation, and returned evidence retains tool, status, UTC timestamp and `mcp:neo4j` provenance. Live reasoning against Aura remains unverified because the configured private credential was rejected; no graph result was invented or substituted.

## Plan-selected retrieval and evidence grading — 2026-10-18

Retrieval now follows the structured planner's validated source selection without unconditional document prefetch. A direct live-database plan invokes its authorized schema capability and invokes `search_knowledge` zero times. Document candidates are graded in one bounded structured-model batch only after the planner selects document search; rejected chunks are excluded from evidence, reasoning context and final sources, with rejection diagnostics retained outside the source list. Missing evidence is routed to another authorized tool, one precise human question or an explicit limitation rather than filled with unrelated documents.

## Public-news routing evaluation — 2026-10-19

The exact prompt `What's going on in Alberta right now?` now classifies as `knowledge/public_web/news` in the deterministic evaluation and enters the bounded public-research node directly. The regression records `search_news` plus selected `fetch_web_page` calls, asserts zero `search_knowledge` and `search_graph` calls, and verifies that dates, publishers, URLs, source links and a multi-line answer longer than 280 characters survive reasoning and finalization. Unit checks cover the `ca-en` region, time window, result bounds, duplicate/unsafe URL filtering, malformed dates and IANA timezone responses.

The live Groq-backed `/ask` acceptance attempt successfully executed DDGS news requests against public providers, then exceeded the 180-second client bound before DeskMate returned a final response. It was not retried. Consequently this revision does not claim a live three-headline final answer, citation quality or bounded end-to-end latency; those remain live acceptance blockers even though deterministic graph behavior, actual public-search reachability and the complete offline/browser suites pass.

## Stopping and structured retrieval evaluation — 2026-10-19

The short `Jake cannot connect to VPN` regression performs one relevant initial source pass, skips irrelevant alert expansion and asks one specific error-message question. A verifier rejection terminates with uncertainty rather than re-reading the same evidence. A separate news regression proves that a fetched, verified answer survives the configured cycle boundary. Existing multi-interrupt cases retain checkpoint resume and record each confirmed human fact without another reasoning cycle after verification.

Synthetic structured-document tests preserve a heading, paragraph, spanned table cells and a labelled figure on the exact source page. They assert stable local object IDs, searchable table/caption text, immutable local image artifacts and reference-only Neo4j parameters. Graph-first retrieval tests assert call order (graph references, local hydration, local semantic ranking), bounded object/relationship constraints and vector fallback. No production Aura writes, Azure Document Intelligence requests, provider inference or spending-control changes were performed for these tests.

## Demo-day research repair — 2026-10-19

The live Groq Express Entry request completes with HTTP 200 after one bounded public-research pass and capacity-aware context compaction. Empty search results do not invoke source evaluation, selected URLs absent from search results are ignored as application validation, irrelevant fetched content is excluded from evidence, and fetch failures remain non-fatal. The live short VPN request also completed with HTTP 200 and asked exactly one question: which device and operating system is being used. Azure OpenAI was not selected or exercised.

## Streaming document upload revision — 2026-10-19

This revision changes only document transport, extraction batching and local artifact persistence. Agentic RAG and reasoning-provider behavior are unchanged. Per explicit instruction, no reasoning evaluation or automated test was run.

## Graph-first evidence routing evaluation — 2026-10-19

Knowledge planning now explicitly selects `search_knowledge` in HYBRID mode for uploaded/internal document questions. That single boundary performs tenant-scoped Neo4j reference discovery first, bounded local object hydration and semantic ranking, then direct Chroma fallback when graph evidence is unavailable, empty or stale. Activity reports the real combined lookup rather than claiming graph success. Public research remains a later route for a genuine external gap and is not a replacement for missing private-document evidence.

Focused stored-evidence checks exercised the production Chroma collection without Azure extraction: the newspaper query resolved stable page objects and the cross-object offer-letter query resolved Chunk and Table objects with page provenance. Aura has verified read connectivity, but writer configuration is absent; the same graph-first component was therefore checked with reference-only in-memory discovery over the real persisted object IDs, and both documents remain honestly marked `graph_pending`. The live missing-knowledge/web check could not execute because external web access was denied by the environment safety review. No answer, graph write or web success was fabricated.

## Recovery/live evaluation — 2026-10-20

- Current Alberta news: **failed before final answer**. Live `search_news` and selected `fetch_web_page` calls executed, but Groq twice returned an invalid `InvestigationDecision`. No headline/snippet was promoted to verified evidence and no answer was fabricated. The bounded recovery was improved to retry the same model in JSON-object mode with local schema validation.
- Uploaded-document inventory: **initial live run failed at verifier structured output; second run reached a real `list_documents` result but asked an invalid clarification**. The response contained 15 persisted inventory rows, while the transient model context retained only eight. Context packing now preserves up to 30 inventory rows and the reasoner contract requires enumerating that authoritative result without blocking valid clarification about which listed document an ambiguous content question means. A final rerun completed classify, plan, reason and verify but was still waiting on observed Groq capacity resets when this record was written.
- Telangana newspaper, cross-source and ambiguous-support checks: **not started**. The shared Groq queue was already imposing multi-minute capacity waits; concurrent requests would not be independent validation and could increase paid/provider load. Existing stored-evidence checks remain historical evidence only and are not relabeled as live passes for this revision.
- Runtime: `/health` returned `status=ok`, Groq selected, local MCP/SQLite/Chroma ready, the official Neo4j MCP integration connected, and the separate direct Aura enrichment flag false; the frontend returned HTTP 200. DeskMate remained running through `make dev`.

Focused regression checks executed in this revision passed (18 selected pytest cases across news flow, MCP shapes, source policy, provider recovery, inventory packing and clarification routing). Legacy full pytest, Vitest, Playwright and benchmark suites were intentionally not run.

## Automatic document-graph reasoning boundary — 2026-10-21

Saved structured uploads expose graph references that resolve only to tenant-owned local Chroma objects; actual text and page evidence remain the basis for answers. Unsaved previews deliberately create no local hydration record, so their Neo4j references produce a stale/unhydrated status rather than content evidence. Planner/reasoner instructions require the original to be uploaded again when the answer needs unretained text. Graph selection remains LLM-directed and optional rather than an unconditional query.

Focused tests verified the reference-only privacy boundary, paragraph/table-cell relationships, versioned IDs, bounded retrieval and saved local hydration without calling Azure or production Neo4j. No live document question is claimed because automatic ingestion remains explicitly disabled and no billable extraction was authorized for this revision.

## Agent activity and evidence-recovery evaluation — 2026-10-21

Focused regressions verify that an evidence-required empty plan receives concrete validation feedback and must select an authorized tool, while a stable general answer is permitted only when the classified source is `general` and the planner explicitly says retrieval is unnecessary. `From knowledge`, `Check knowledge` and `According to my uploaded PDF` override an erroneous general-source classification. Verifier gaps route to a fresh plan only while the configured investigation-cycle budget remains; the terminal route retains the specific limitation.

The exact NSNP local-knowledge request ran through FastAPI, LangGraph, LangChain and MCP with isolated persisted test data and invoked `search_knowledge`. Activity and frontend regressions passed, including the absence of decorative question marks and the presence of a real recovery event. No live Groq/Azure reasoning, live current-news acceptance, production document mutation or external public-web retrieval was performed, so those remain live acceptance blockers.

## General intelligence benchmark — 2026-10-21

`backend/app/tests/general_intelligence_cases.json` freezes 40 cases across time, timezone/DST, dates, weather, news, places, travel, math, units, currency, holidays, stable knowledge, documents, multi-tool work, ambiguity and provider failure. `scripts/evaluate_general_intelligence.py` compares capability readiness against baseline `c2b7e60`: 8/40 (20%) baseline and 40/40 (100%) candidate. This is intentionally reported separately from model tool-selection accuracy; no paid Groq or Azure call was authorized for this evaluation.

Forty-plus deterministic/MCP checks passed for numerical correctness, argument schemas, read-only annotations, authorization, source metadata and identical-failure suppression. Free live calls successfully returned dated Halifax weather, a CAD/USD ECB reference conversion, a Halifax–Moncton driving route, Canadian Thanksgiving and Halifax place results. The combined live probe took 4.0 seconds and used no model tokens or billable API. Ratings, opening hours, traffic, weather alerts, cycling/walking routes and GNews remain unavailable and are never claimed. Existing news discovery/article hydration and document retrieval were retained rather than duplicated.

## Semantic memory evaluation — 2026-10-22

Focused deterministic scenarios cover explicit name capture, restart persistence, duplicate suppression, correction/supersession, graph-unavailable queuing, tenant/owner isolation, per-request opt-out, unrelated third-person text, revocation and mocked graph delivery. Profile retrieval is an ordinary bounded MCP read selected only when applicable; ordinary calculations and unrelated questions do not automatically query semantic memory. Documents continue to use the separate source-grounded structured-reference graph and cannot silently rewrite authenticated profile facts.

The focused semantic-memory/graph suite passed 14 tests, 31 related MCP/security/document-job tests passed, the Knowledge Panel suite passed four tests, and the production frontend build passed. The evaluation used temporary SQLite and an in-memory graph sink. It did not call a reasoning provider, Azure Document Intelligence or production Neo4j, and therefore does not claim live extraction quality, live Aura persistence or end-to-end model selection of the new profile tools.

## Persistent conversation and general personal-memory evaluation — 2026-10-25

The deterministic acceptance coverage now includes checkpoint-backed conversation listing/restoration, React active-thread restoration, explicit general facts, semantic/paraphrased recall, correction history, duplicate suppression, optional graph delivery, individual forgetting and owner isolation. Personal memories enter reasoning in a dedicated state field and remain distinct from operational case facts and retrieved document/web evidence.

Focused non-billable checks completed before the final no-test instruction: 11 general-conversation checks, 15 React App checks and nine semantic-memory checks passed. No later full regression, build, browser or live integration run is claimed. Live-provider answer quality and live Aura reconciliation remain opt-in acceptance work.

## Document semantic graph evaluation status — 2026-10-25

The new reasoning contracts separate source-grounded document extraction from conservative graph placement. Extraction outputs retain logical object references; placement must choose reuse, creation or unresolved exactly once per entity and cannot name an existing ID outside the bounded tenant candidate set. Graph-first retrieval converts entity-linked document references back through `LocalKnowledgeStore.resolve_object_ids`, so Neo4j remains discovery context and Chroma text remains answer evidence.

No evaluation command was executed because this task explicitly prohibited tests, lint and builds. No live model or Aura claim is made. A later authorized acceptance run should cover multi-section resume, duplicate-entity reuse across uploads, unresolved mentions, conflicting source claims, stale reference reporting, tenant isolation and vector-only behavior during Aura outages.

## Human-readable investigation activity — 2026-10-25

Existing SSE activity is now reduced into Understanding, Planning, Searching, Evaluating, Verifying and Outcome sections without an additional model call. Repeated low-level events update their stage, while the full allowlisted operational log remains expandable and is restored with the owner-scoped conversation record. Python compilation and TypeScript no-emit checking passed; no reasoning tests or live model calls were run. Natural-language fidelity against live Groq/Azure event combinations remains an opt-in acceptance check.

## Chat transcript and URL-ingestion evaluation — 2026-10-26

Focused React checks cover chronological multi-turn rendering, current-summary deduplication, follow-up submission and the Knowledge ingestion status contract. A live headless browser confirmed the transcript/activity desktop split, workspace reset menu and safe Cancel path, plus Knowledge and Voice tab accessibility. Four isolated backend checks exercised public-web authorization, durable local indexing, retrieval and graph-linked reporting using temporary stores.

The live service reported local MCP/private stores ready and URL ingestion enabled, while direct Aura enrichment was unavailable. One bounded `example.com` request returned HTTP 400 and produced no searchable local item; it was not repeated. This revision therefore claims the repaired deterministic ingestion boundary, not a successful production crawl or live graph projection. No reasoning provider, paid Azure operation or full regression suite was invoked.

## Validation and personal-memory recovery evaluation — 2026-10-26

One bounded live Groq request reproduced the failure at the `plan` node: classification completed as knowledge/general, no MCP capability executed, and application validation rejected both the corrected and final plan. This rules out tool execution, MCP mutation and evidence-reference validation for the reported incident. Because the earlier error boundary erased the validator detail, the exact historical argument names cannot be reconstructed without another paid request and are not claimed.

Disposable deterministic checks now cover an empty authenticated profile returning a concise unknown-name answer, a confirmed fictional name returning through the existing semantic-memory/profile path, stable general knowledge using zero tools, a schema-compliant MCP retrieval, invalid profile arguments corrected to the discovered zero-argument contract, and invented evidence IDs corrected once then downgraded safely. All focused checks passed. The live request was not repeated after implementation; provider-specific production answer wording remains unclaimed.

## Live semantic document activation — 2026-10-26

The enabled semantic worker successfully produced and persisted a source-grounded proposal for an existing authorized URL document. A read-only worker-identity check resolved every referenced logical object ID from tenant-scoped Chroma, ruling out missing evidence, owner scope and local-vector connectivity. Conservative graph-placement validation rejected the subsequent model result before semantic entity writes, so semantic placement remains a live blocker. The successful paid calls were not automatically retried.

The focused activation regression and existing plain-text automatic-ingestion check passed (2 tests). Python compilation and whitespace validation also passed. No full suite, new Azure extraction, workspace reset or destructive operation was run.

## Document GraphRAG repair evaluation — 2026-10-27

Focused offline retrieval and graph checks passed 16 scenarios covering stable logical object IDs, structural references, Chroma hydration, stale/unavailable graph fallback, evidence-bound placement and graph writer effects. Saved-document routing checks confirmed structured document intent and mandatory graph participation; planned local searches cannot remain keyword-only and use `HYBRID` graph-first retrieval.

Worker/job checks passed 22 focused scenarios across immediate wakeup, startup recovery, bounded unchanged-job handling, deferred structural retries and durable semantic handoff. Five Knowledge Panel checks and the production frontend build passed, including persisted status polling through structural and semantic completion. No live provider, Azure Document Intelligence request or Aura mutation was made, so live extraction quality and production semantic placement remain unverified.

## NSNP live recovery — 2026-10-27

After correcting Azure bounding-region serialization, the preserved NSNP job resumed through extraction, local indexing, structural linking and semantic understanding. The semantic provider produced 14 grounded entities/mentions and no supported relationship claim; zero relationships is retained rather than inventing an edge. Persisted status is `ready`/`complete`, with both graph statuses `linked` and no stale processing error.

The focused SDK-shape regression passed. The live run exercised Azure Document Intelligence, local SentenceTransformers/Chroma, the dedicated Neo4j writer and the configured Groq provider once through the recovered pipeline. No unrelated document was reset or reindexed by this repair.

## Mandatory saved-knowledge graph participation — 2026-10-26

The LangGraph knowledge subgraph now records explicit saved-knowledge scope in durable state and executes a dedicated retrieval node. It runs local `search_knowledge` and an application-authored tenant-scoped Neo4j read concurrently, preferring the official read-only MCP tool when authorized. Empty graph matches remain valid observations; unavailable graph access is emitted as a failed activity and does not falsely claim graph evidence or suppress local Chroma results. Ordinary general questions and public-web requests do not enter this mandatory path.

Offline coverage verifies classifier override, required local retrieval, official-MCP preference, fallback graph invocation, activity visibility, tenant parameterization and unchanged general routing. Conservative graph placement preserves every proposed entity as created, reused or unresolved and prevents unsupported reuse IDs. No paid reasoning evaluation or live semantic regeneration was run.
