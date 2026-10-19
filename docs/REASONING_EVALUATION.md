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
