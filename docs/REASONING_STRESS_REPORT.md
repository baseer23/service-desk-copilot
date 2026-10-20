# DeskMate Live Reasoning Stress Report

Run started: 2026-10-19 (America/Halifax)  
Application commit at start: `d089e5e3276056b98e8d5fe87d405e398186e2c5`  
Mode: real local DeskMate application, Groq `openai/gpt-oss-120b`, LangGraph and MCP; no mocks or fixture answers.

## Resume checkpoint

- Latest completed investigation: 3 (source-policy recheck pending)
- Next investigation: fresh re-run of 3 with authoritative-source selection, then 4 — printer troubleshooting
- Current hypothesis: named technical standards and protocol behavior need primary documentation when model recall is unreliable.
- Completed fixes during this run: bounded post-verification revision; human-evidence cycle reset; repeated-question rejection; state-aware stale-clarification validation; verifier contract normalization; primary-source guidance for named protocols/standards.
- Live constraints: Groq shared request queue is imposing roughly 31 seconds of capacity wait between some stages. The cybersecurity PDF original is preserved, but extraction is `error / extract_inflight` with zero pages/chunks and no matching Chroma metadata.

## Baseline

- Git: clean `main`, synchronized with `origin/main` at `d089e5e`.
- APIs: backend `8000` and frontend `5173` responding.
- Provider: Groq selected; health reports reasoning configured. Local SentenceTransformers embeddings are ready.
- MCP: connected; configured Neo4j MCP is connected and read-only. Readiness reports Neo4j reads and structured ingestion available. The health endpoint's separate direct-Aura flag reports disconnected, so these statuses must not be conflated.
- Local stores: SQLite and Chroma ready. No database was reset.
- Corpus: 15 document records; 9 ready, 1 graph-pending but locally indexed, 1 saved/unprocessed, and 4 extraction errors. Ready documents retain page/chunk metadata. Telangana Today is graph-linked. The offer letter is locally indexed with page/table provenance but graph-pending.
- Cybersecurity paper: `2610.21254v1.pdf` exists only as a preserved original. Its record has zero pages/chunks and no Chroma metadata. Questions requiring it are unavailable-source scenarios; paid extraction was not repeated.
- Financial/safety: existing spending guards remain enabled and unchanged. No Azure extraction was invoked.

## Progress

| # | Question | Result | Root cause | Code change | Next challenge |
|---:|---|---|---|---|---|
| 1 | What is a vector database, and when should I use one? | PASS (trace; final payload capture interrupted) | Correct direct-knowledge plan; no retrieval warranted. Groq queue added latency. | None | Confirm direct behavior on a comparison question. |
| 2 | Explain the difference between RAG and fine-tuning. | PASS | Correct direct-knowledge plan and grounded general answer; no tool call. | None | Explain MCP versus REST. |
| 3 | What is MCP, and how does it differ from an ordinary REST API? | FAIL; source recheck pending | Recovery and resumability were repaired, but unsupported model recall still misdescribed the protocol and passed verification. | Recovery/resume repairs plus authoritative-primary-source guidance for named technical standards. | Fresh source-selection recheck. |

## Investigation details

### 1 — Vector databases

- Conversation: transient request `d67df737ccae4743a364fff1cab45f8f`
- Expected: direct stable explanation; zero retrieval calls.
- Observed: knowledge classification; context inventory; planner selected zero tools with `additional_evidence_useful=false`; empty execute step; reason and verification completed. Usage: classify 815 tokens, plan 3,410, reason 4,441, verify 1,744. A streamed client capture ended after a keepalive, but provider accounting and the final checkpoint show completion.
- Assessment: tool discretion was correct. The context inventory is planner awareness, not evidence retrieval. The main weakness is latency/cost: four model calls for a simple question plus a roughly 31-second provider-capacity wait.
- Code action: none; changing the orchestration solely for this latency observation would risk bypassing the required planner/verification contracts.

### 2 — RAG versus fine-tuning

- Conversation: `f5c579ae395e4cb8aeaf68cda1eea78a`
- Expected: direct stable explanation; no private or public retrieval.
- Observed: knowledge workflow, zero selected tools, one reasoning pass, one verification pass, final confidence 0.95. The answer clearly contrasted knowledge location, update cycle, resource requirements, use cases, and combined use. Evidence contained only the user request; the verifier correctly treated this as permitted stable general knowledge.
- Assessment: PASS. No hallucinated source access, citations, unnecessary MCP search, or repeated loop.
- Code action: none.

### 3 — MCP versus REST

- Conversation: `ca8122ae49e349119cce4612414cb56b`
- Expected: explain Model Context Protocol versus REST in the DeskMate context, or clarify genuine acronym ambiguity; retrieval optional.
- Observed: planner selected a direct answer. The reasoner incorrectly expanded MCP as “Microsoft Cloud Platform” and added unsupported protocol, identity and versioning claims. Verification correctly rejected six claims with confidence 0.32 and requested continued research. The graph nevertheless finalized `insufficient_information` with a null answer because non-clarification verification was terminal.
- Assessment: FAIL. Hallucination resistance worked at verification, but recovery and user experience failed.
- Root cause: `verification_route()` ignored `should_continue_research` for ordinary rejected answers. There was genuine new information (the verifier's claim-level rejection), but no bounded path back to model-directed source selection.
- Code action: allow exactly one verifier-informed revision when `research_iteration < 2`; retain terminal behavior after the second verification. Reasoning instructions now require the model to revise, retrieve or clarify rather than repeat rejected claims.
- Recheck: the repair correctly produced `needs_input` with “What does MCP stand for in your context?” and `clarification_valid=true`. Resuming the same checkpoint with “Model Context Protocol” preserved the human evidence, but the next planner/reasoner repeated the identical clarification and interrupted again.
- Second root cause: `ask_human()` retained the exhausted `research_iteration`, and neither planner nor reasoner deterministically rejected an already-answered question.
- Second code action: human evidence resets only the bounded reasoning/verification pass (not the thread or evidence); repeated exact clarifications are validation issues and are not reissued.
- Second recheck: the identical clarification was no longer shown. The model used the confirmed expansion and generated an answer, but its stale clarification-only plan kept direct-answer support disabled. The verifier also returned unsupported claims while incorrectly setting `should_continue_research=false`, so finalization safely withheld the answer.
- Third code action: reject an already-answered clarification during `plan()` validation and give the planner one corrective retry using the confirmed answer. Normalize non-clarification verification with unsupported claims to `should_continue_research=true`, activating only the already-bounded revision edge.
- Third recheck: a same-thread follow-up retained the confirmed expansion, did not repeat the question, and completed in one reasoning pass. Context/resume behavior therefore passed. The answer was still substantively wrong: it described MCP as a framework for persistent conversation state and invented confidence/error semantics. Verification accepted those claims as stable general knowledge.
- Fourth root cause: the selected model's unaided knowledge of a named technical protocol was unreliable, while planner policy allowed direct recall.
- Fourth code action: source-selection instructions now classify detailed claims about named protocols, standards and APIs as precise sourceable claims and prefer authorized primary documentation. This is a general evidence rule, not an MCP-specific answer or keyword route.

## Source-grounded reference notes

- Only ready or graph-pending documents with local indexes are eligible for fact evaluation.
- Raw retrieval candidates are not evidence until the evidence grader accepts them.
- The unavailable cybersecurity PDF has no established reference answers in this run.

## Unresolved defects and risks

1. Simple direct questions currently require classify, plan, reason and verify model calls; the shared Groq capacity queue can make a correct answer take over 30 seconds.
2. Chroma emits a non-blocking PostHog client compatibility warning during startup/search. It does not currently affect retrieval readiness.
3. The cybersecurity PDF cannot be evaluated until its interrupted paid extraction is explicitly reviewed or a safe local extraction path is authorized.
