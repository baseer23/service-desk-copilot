# Probabilistic knowledge-graph construction: experiment report (dev_exp)

Branch `dev_exp`, based on `dev` at `1ad9b0a`. Code: `backend/app/experiments/probabilistic_kg/`. Test: `backend/app/tests/test_probabilistic_kg.py`. Re-run the evaluation offline with:

```bash
DESKMATE_TEST_PROFILE=offline TEST_MODE=true GENERATION_PROVIDER=stub EMBED_PROVIDER=stub \
NEO4J_URI= MCP_SERVERS='[]' .venv/bin/python -m backend.app.experiments.probabilistic_kg.evaluate
```

## What this measures, and what it does not

Every number below comes from an offline run. **No real model was called.** Each document's "model reading" is a recorded structured response (`fixtures.MODEL_READINGS`) that I wrote to resemble realistic LLM output, including deliberate mistakes. Approaches A and B receive *the same reading*, converted to each pipeline's schema. So the comparison measures:

- **what each pipeline keeps, represents, rejects and retrieves**, given the same model reading.

It does **not** measure:

- whether the richer B prompt makes a real model read documents better. That needs a live run (see *Next steps*).
- real Cypher execution or the real `neo4j-mcp` binary. The B and C statements ran against an offline engine (`sink.py`) that executes only the experiment's fixed templates. C used a real MCP stdio connection, but to a stand-in server (`fake_neo4j_mcp.py`) with the same tool names and read-only annotations as `neo4j-mcp`.

The dataset is small: 6 documents, 16 expected relationships, 10 questions. Treat the results as evidence about mechanisms, not as benchmark accuracy. Two adjustments were made after the first run:

- Job roles are no longer document-scoped. "Warehouse Supervisor" had become two entities.
- The answer key now also accepts the board minutes as a source for the agreement-party relationship. They do state it.

## The three approaches

| | A: dev baseline | B: probabilistic + driver | C: probabilistic + neo4j-mcp |
|---|---|---|---|
| Structure | dev's `_publish_reference_objects`, unchanged | same | same |
| Understanding | `DocumentGraphUnderstanding`: batched LLM calls over chunk-level objects, then consolidation | `ProbabilisticGraphReasoner`: one call per document over element-level evidence and the entities already in the graph | same proposals as B |
| Identity | `GraphDiscovery` (name, alias, identifier) and an LLM placement decision | deterministic: grounded identifier, or exact name and type; persons, dates and values stay document-scoped | same as B |
| Validation | evidence IDs must exist; form-value guard | `ProposalValidator` (below) | same as B |
| Claim status | none; every relationship is a `RELATED_TO` fact (`is_explicit` flag only) | observed / derived / hypothesis; hypotheses use a separate label | same as B |
| Writes | `GraphRepository` per-item transactions | 13 template statements in one driver transaction | the same 13 statements, one `write-cypher` call each, on a separate write-enabled connection |

Approach A was run through dev's real `DocumentGraphUnderstanding`, `GraphDiscovery` and `GraphPlacementService`, against an in-memory graph whose candidate discovery mirrors the Neo4j query.

## Probabilistic graph construction (B and C)

1. **Evidence.** `EvidenceIndex` runs dev's structural projection against a recording graph. Evidence IDs are therefore exactly the reference-object IDs dev writes to Neo4j and Chroma. Each ID is joined back to its Azure DI element:
   - text, page, span, regions and confidence;
   - for table cells, headers that take merged cells into account;
   - for form fields, the question, options, extracted selection and status.
2. **Reasoning.** The model receives the whole document's evidence, the extracted form answers and the entities already in the graph. It returns a `ProbabilisticGraphProposal` containing:
   - document type and concepts;
   - entities with evidence IDs;
   - relationships labelled `observed`, `derived` or `hypothesis`, with confidence and rationale;
   - form readings;
   - contradictions;
   - same-identity links.

   A relationship may point at an existing entity (`existing:<key>`). That is how cross-document derivations are expressed, for example "Maria Chen ELIGIBLE_FOR Group benefits", which joins a form answer to a policy clause.
3. **Validation.** `ProposalValidator` decides what is written:
   - **Cited evidence must exist.** A relationship may cite other documents but must cite its own.
   - **Entities must be grounded.** An entity's name or alias must appear in cited evidence, otherwise it is rejected.
   - **Identifiers must be grounded.** An identifier is used for identity only if the cited evidence contains it.
   - **Claim status follows the evidence.** An `observed` claim needs one cited element mentioning both endpoints. Otherwise it is demoted to `derived` (both endpoints grounded in different elements) or `hypothesis`.
   - **Status is never raised.** Confidence has no effect on status; the test sets every confidence to 1.0 and checks this.
   - **Form claims must match the check-box.** A claim or reading that disagrees with the extracted selection is rejected.
   - **Conflicts are flagged.** A single-valued predicate with different objects is marked contested. A model-reported contradiction contests only claims that actually disagree.
4. **Write plan.** `compile_plan` turns the validated graph into tenant-scoped, idempotent `UNWIND … MERGE` statements built only from fixed templates. There is no `DELETE`, `DETACH`, `REMOVE`, `DROP` or `CALL {}`, which is checked when the module loads. Rejected proposals are kept in the validation report for debugging and never written.

## Neo4j MCP integration

- **Conversational agent:** unchanged. It still sees only the read-only `neo4j-mcp` (`get-schema`, `read-cypher`).
- **Experiment writer:** `restricted_writer_client` is a separate MCP connection that is never registered with the agent.
  - It accepts only `write-cypher`, `read-cypher` and `get-schema`.
  - It starts `neo4j-mcp` with `NEO4J_MCP_READ_ONLY=false` and credentials from `DESKMATE_EXP_NEO4J_*` only.
  - It refuses to start if the experiment URI equals the application's `NEO4J_URI`.
- **What may be sent:** `McpGraphWriter` sends only exact template text. Anything else, including model-written Cypher, is refused before reaching MCP.
- **Verification:** after writing, it reads back every expected ID with `read-cypher`.
- **Failures:** a failure is reported, never retried, and the count of statements already applied is recorded.
- **Unused tools:** `list-gds-procedures` was not used. No experiment here needed Graph Data Science.
- **Atomicity:** each `write-cypher` call is its own transaction. B's driver path is one atomic transaction; C applied 13 separate ones.

## Example graph structures

The same two documents, the vendor agreement and the board minutes, in each approach:

```text
A (dev):
(Vendor Agreement VA-2024-017)-[:RELATED_TO {EFFECTIVE_DATE}]->(2024-02-01)
(Vendor Agreement VA-2024-017)-[:RELATED_TO {EFFECTIVE_DATE}]->(2024-03-01)      no conflict marker
(Maritime Mutual Insurance)-[:RELATED_TO {SUBSIDIARY_OF}]->(Harbour Logistics Inc.)   speculation stored as a fact
(Harbour Logistics Incorporated)   a second entity: reuse never adds the business number

B / C:
(GraphClaim {EFFECTIVE_DATE, status:derived, contested:true})-[:SUPPORTED_BY]->(Paragraph ven-1)
(GraphClaim {EFFECTIVE_DATE, status:observed, contested:true})-[:SUPPORTED_BY]->(Paragraph min-2)
(Contradiction)-[:INVOLVES]->(both claims); (Contradiction)-[:SUPPORTED_BY]->(ven-1, min-2)
(GraphHypothesis {SUBSIDIARY_OF})-[:SUBJECT]->(Maritime Mutual), -[:OBJECT]->(Harbour Logistics)   no RELATED_TO edge
(Harbour Logistics Inc. {aliases:[Harbour Logistics Incorporated], identifiers:{business_number}})
```

Form fields keep dev's existing structure in all three: `(Page)-[:HAS_FORMFIELD]->(FormField)-[:HAS_SELECTIONMARK]->(SelectionMark {state_flag})`. Semantic claims about a form answer cite the FormField.

## Results (offline, recorded model output)

### Graph quality

| Metric | A | B | C |
|---|---|---|---|
| Entities (expected 16) | 21 | 18 | 18 |
| Entity precision | 0.81 | 0.89 | 0.89 |
| Entity recall | 1.00 | 1.00 | 1.00 |
| Duplicate entities | 1 | 0 | 0 |
| Facts written | 20 | 17 | 17 |
| Relationship precision | 0.80 | 0.94 | 0.94 |
| Relationship recall | 0.94 | 0.94 | 0.94 |
| Unsupported facts (hallucinated) | 4 | 1 | 1 |
| Hypotheses kept apart from facts | 0 | 2 | 2 |
| True facts held only as a hypothesis | 0 | 1 | 1 |
| Attribution, element-exact | 0.06 | 1.00 | 1.00 |
| Attribution, element or its chunk/table | 1.00 | 1.00 | 1.00 |
| Mean cited text per evidence (characters) | 184 | 62 | 62 |
| Contradictions detected (of 1) | 0 | 1 | 1 |
| Ingestion model calls | 19 | 6 | 6 |

The two recall misses differ:

- **A's miss** is the cross-document "eligible for group benefits" relationship, which dev's schema cannot express.
- **B's miss** is "Maritime Mutual Insurance headquartered in Halifax". The recorded reading cited the wrong paragraph, so B demoted it to a hypothesis rather than state it on the wrong evidence. A kept it as a fact with that wrong citation.

The unsupported facts:

- **A (4):** the invented "Nova Scotia Labour Standards Code" link, the invented "Warehouse Manager", the speculative "subsidiary" link, and "Maria Chen EMPLOYMENT_TYPE Permanent".
- **B (1):** "Maria Chen EMPLOYMENT_TYPE Permanent". It cites the form question whose extracted answer is **No**, but names no option label. B checks grounding and form answers, **not predicate meaning**, so it gets through. This is the clearest remaining gap.

Validation activity in B: 2 entities rejected, 3 relationships rejected, 1 of 3 form readings rejected (the misread check-box), and 3 status demotions. dev's form-value guard also rejected the misread "answer is Yes" relationship in A; that protection already exists on `dev`.

### Answers

| Question set | A, one-hop | A, agentic | B/C, one-hop | B/C, agentic |
|---|---|---|---|---|
| Exact form/table (4) | 4/4 | 4/4 | 4/4 | 4/4 |
| Multi-hop and relationship (6) | 2/6 | 3/6 | 4/6 | **6/6** |

- **Exact questions:** check-box, table cell and merged cell are answered by indexed lookup in every approach, with the correct evidence. They never reach the planner (0 model calls).
- **M2, the long manual:** the evidence is on page 9 of 10, behind a second hop. Agentic retrieval solves it everywhere; one-hop lookup fails everywhere.
- **M3, cross-document:** only B/C have the relationship.
- **M4, conflicting dates:** B/C answer `CONFLICT` with both dates. A silently returns 2024-02-01.
- **M6, "Is Maritime Mutual a subsidiary…?":** B/C answer `NOT_ESTABLISHED`. A answers `YES` from the stored speculation.

Retrieval cost for the six questions, agentic mode:

- **A:** 13 graph operations, 6 passages hydrated, 19 model calls.
- **B/C:** 14 graph operations, 7 passages hydrated, 20 model calls.

Fan-out is capped at 5, hops at 3, and visited nodes are skipped. A cycle test checks these bounds.

### Structural completeness (shared by all three)

Every Azure DI element in the fixtures can be reached from the graph:

| Element | Reachable from the graph |
|---|---|
| Paragraphs | 72/72 |
| Table cells | 12/12 |
| Tables | 1/1 |
| Figures | 1/1 |
| Selection marks | 6/6 |
| Pages | 17/17 |

Some attributes are on the graph nodes and some are only in the saved extraction artifact:

- **On graph nodes:** page, regions, reading order, first span, selection state and confidence, cell row/column, paragraph role.
- **Artifact only:**
  - merged-cell spans, cell header kind;
  - paragraph and cell confidence;
  - DI model and API version.

  These are all recoverable through `source_element_id` and row/column, and the evidence index uses them (merged-cell headers). Nothing in the original extraction is overwritten.

### Write transport (B and C)

| | B: Python driver | C: neo4j-mcp `write-cypher` |
|---|---|---|
| Statements | 13 | 13 |
| Write calls | 13 | 13 |
| Transactions for the writes | 1 (plus 1 read-back) | 13 |
| Atomic | yes | no |
| Read-back verification | 18/27/17/2/2 of 18/27/17/2/2 | same |
| Resulting graph | identical (66 semantic nodes, 200 relationships) | identical |

C's verification proved useful during development. A response-envelope parsing bug made every write count read as 0, and verification reported `ok: false` rather than claiming success.

## Strengths and weaknesses

**A (dev):**
- *Strengths:* simple; batches long documents into bounded calls; the existing form-value guard works.
- *Weaknesses:*
  - every proposal becomes a fact, so speculation and invented entities survive;
  - no conflict detection;
  - reuse never enriches entities, which caused the duplicate;
  - chunk-level citations (184 characters on average) can't point at the exact paragraph or cell;
  - cannot express cross-document derivations.

**B (probabilistic + validation, driver writes):**
- *Strengths:*
  - fewer unsupported facts on the same reading;
  - hypotheses are stored but not traversable;
  - contradictions are explicit and surfaced in answers;
  - element-exact citations;
  - cross-document links and identity resolution;
  - a single atomic transaction.
- *Weaknesses:*
  - **predicate meaning is not verified**;
  - strictness costs recall when a citation is wrong (Halifax);
  - one call per document is not bounded by context size; production use needs dev's batching;
  - identity resolution by exact name can still merge same-named organizations.

**C (same as B, MCP writes):**
- *Strengths:*
  - the same graph as B;
  - one tool surface for reads and writes;
  - the allowlist and verification work over MCP.
- *Weaknesses:*
  - no multi-statement atomicity, so a failure mid-plan leaves a partial (MERGE-idempotent) graph;
  - one more process and credential set to secure;
  - write-enabled MCP is exactly the capability that must never reach the conversational agent.
- On this run it showed no flexibility or reliability benefit over the driver. **The graph intelligence is in the reasoning and validation, not in the transport.**

## Does a hybrid beat the baseline?

On this dataset and recorded reading, yes: B/C is the hybrid. It combines probabilistic proposals, deterministic validation, deterministic exact lookups and bounded agentic multi-hop retrieval. Compared with A it gives:

- higher relationship precision (0.94 vs 0.80);
- fewer unsupported facts (1 vs 4);
- exact citations;
- conflict detection;
- 6/6 vs 3/6 relationship answers;
- the same recall (0.94).

**Recommended architecture:**
- **Understanding:** dev's batched understanding, extended with B's schema (claim status, element-level evidence, `existing:` links, form readings, contradictions).
- **Validation:** B's validator, plus a predicate-entailment check against the cited evidence, for example a second, separate model judgement.
- **Writes:** the Python driver, for atomicity.
- **Retrieval:** read-only MCP, exact lookups first, then bounded agentic traversal over verified facts only.

## Next steps that need approval

1. **Live model run:** real Groq or Azure OpenAI proposals for these six fixtures and NSNP 200's saved extraction, scored against the same answer key. This is paid, bounded to about 7–10 calls.
2. **Real Cypher and real neo4j-mcp:** run B and C against a local Neo4j in Docker, isolated from Aura. The Docker daemon is not running on this machine and no Neo4j image is present.
3. **An isolated Aura database** with separate `DESKMATE_EXP_NEO4J_*` credentials, if Aura-specific behaviour matters.
