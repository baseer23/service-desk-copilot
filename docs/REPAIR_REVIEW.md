# DeskMate repair and activation status

Reviewed against commit `14246d8` on 2026-10-13.

The application is usable for local document extraction, document saving, cited
keyword retrieval and case management at **http://127.0.0.1:5173**. Full Azure
investigation and voice activation remain incomplete. No new paid inference,
embedding, Voice Live connection or physical microphone test was performed.

## Confirmed problems and repairs

- Investigation controls were disabled without a nearby explanation. They now
  explain the usage shutdown and last workflow failure. **Save request locally**
  creates a real uninvestigated SQLite case through MCP; cases can be reopened and
  investigated explicitly when reasoning becomes available. **Refresh app status**
  rechecks the actual backend.
- Structured FastAPI errors were reduced to a generic HTTP number in the UI. Safe
  `detail.message`, error codes and input-validation messages now appear.
- The acceptance policy rejected PDFs before extraction; embedding failures were
  misreported as PDF parsing failures. Local extraction/saving is now independent
  of reasoning, embeddings and the Azure emergency switch. Parse, policy and
  embedding errors remain distinct.
- Native PDF extraction rejects unreadable documents instead of reporting a
  successful ingestion of zero chunks. Parsing runs in a separate process with a
  35-second deadline, 5 MB input limit and 20-page limit. UTF-8 `.txt` and `.md`
  documents also work. Poppler `pdftoppm` and Tesseract enable optional local OCR;
  neither is installed on this machine, so scanned PDFs explain that dependency.
- A new SQLite document/FTS index stores extracted text and chunks atomically,
  deduplicates by exact SHA-256, and survives restart. It adds tables without
  deleting or replacing cases, facts, events or semantic collections. Simultaneous
  duplicate saves do not create duplicate chunks.
- Explicit keyword retrieval stays in `rag/retrieve.py` behind MCP. It searches
  staged documents and reads the existing verified Chroma collection without a new
  query embedding. Results show document titles, actual excerpts and source IDs;
  they are labeled excerpts, not generated answers. Existing Chroma keyword lookup
  checks bounded substring candidates; it is not a substitute for semantic search.
- **Add to knowledge** and per-document **Index** remain explicit semantic writes
  gated by Azure readiness and acceptance policy. Local saving does not mint fake
  vectors or silently switch providers. Staging/indexing tools are excluded from
  model-selected write plans.
- Azure requests duplicated strict schemas in both system instructions and
  `response_format`. The schema is now sent once; display metadata, empty context
  and JSON whitespace are removed while canonical fields, constraints, evidence,
  provenance, false values and zero values remain. JSON-mode tool plans still
  receive their schema and undergo canonical validation. Real fields named
  `title`, `description` or `default` are preserved.
- Azure embeddings rejected an entire document with more than four chunks. They
  now dispatch batches of at most four, preserve output order and per-batch usage
  reservations, and stop further dispatch on emergency disable or a document-wide
  deadline. No Chroma upsert occurs until all document embeddings succeed.

Regression tests cover the repairs, including real multipart PDF extraction,
HTTP/MCP round trips, persistent SQLite/Chroma, concurrent saves, preserved cases,
write-tool rejection, structured UI failures, schema preservation, embedding batch
accounting and no dispatch after an expired deadline or emergency shutdown.

## What works and how it was verified

| Feature | Normal running application | Isolated acceptance stack |
| --- | --- | --- |
| PDF extraction and local document saving | Real browser, HTTP, MCP, parser and SQLite passed | Passed |
| Document deduplication/restart | Real saved document remains available | Real stores tested |
| Cited keyword excerpts | Real local document and preserved Azure-indexed fixture passed | Passed |
| Case create, list, reopen | Real SQLite/MCP passed | Passed, including simultaneous cases |
| Automated MFA, VPN, printer and Outlook investigations | Blocked by Azure readiness | Real browser/LangGraph/MCP with explicitly simulated reasoning passed |
| Clarification, continue/resume and remembered facts | Blocked by Azure readiness | Existing real HTTP/checkpoint/MCP browser tests passed |
| PDF → chunks → embeddings → Chroma → retrieval → cited answer | Azure-backed full answer remains blocked | Isolated deterministic embeddings/reasoning with real extraction, Chroma and MCP passed |
| Internet research escalation | Disabled by the current acceptance policy | Bounded research/provenance tests use explicit external-service mocks |
| Voice/transcription/audio | Disabled; transport validation still incomplete | SDK serializer, auth, PCM, cancellation and synthetic-browser checks passed |

The direct browser check of the normal application used **no request interception
or model mocks**. It verified local extraction/saving/search, reading the previous
Azure-vector fixture, case save/reopen, policy explanations, status refresh and
emergency disable. It recorded **zero JavaScript exceptions, zero failed local
network requests, zero microphone requests and zero additional usage estimate**.
Only fictional acceptance data was added. Source/customer text and credentials
were not printed or committed.

## Azure evidence and remaining blocker

Read-only Azure CLI verified the existing resource/deployments and budget:

- `deskmate-reasoning`: `gpt-4.1`, capacity **1**, **1,000 TPM**, **1 request/60 seconds**.
- `deskmate-embedding`: `text-embedding-3-small`, capacity **1**, **1,000 TPM**,
  **1 request/10 seconds**.
- `deskmate-total-monthly-10-cad`: CAD **10**, Monthly, all five notifications
  enabled at Actual 50/80/100 and Forecast 80/100. Current reported spend was
  CAD **0**, subject to delayed billing.
- CAD **8** estimated application allowance and CAD **2** setup ceiling retained.
  Persistent emergency shutdown and the private environment shutdown remain active.
  No deployment, budget, role, key, subscription or other Azure resource was changed.

Offline measurement used the actual discovered MCP tool schemas, illustrative
fictional evidence and `o200k_base` tokenization. These are request-size estimates,
not Azure billable counters or a certificate of capacity adequacy:

| Stage | Previous input estimate | Optimized input estimate | Output ceiling |
| --- | ---: | ---: | ---: |
| Classify | 451 | 316 | 220 |
| Analyze | 823 | 402 | 600 |
| Plan | 1,835 | 1,426 | 800 |
| Decide | 2,896 | 2,154 | 800 |
| Verify | 1,276 | 824 | 800 |

The illustrative largest stage needs about **2,954 tokenized input/output tokens**;
the separate character estimate yields about **3,336**. Thus **4,000 TPM is a
planning floor for this sample**, not a proven minimum for arbitrary documents,
larger evidence sets or concurrent users. Exact Azure admission cannot be
established without an authorized live experiment. Microsoft documents that quota
admission uses prompt size and the output ceiling and can differ from exact
tokenizer/billing counts. [Azure quota and rate-limit documentation](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/quota).

The current **1,000 TPM cannot accommodate the full measured workflow**. No
capacity increase was requested or executed, no shutdown was cleared, and no
further failed paid trial was repeated. The optimized classifier alone fitting an
estimate would not prove the remaining workflow works.

Voice still requires successful text reasoning and corrected input/output
transport validation before a microphone session. Its previous two failed trials
and the initial manual-VAD fix remain documented in
[AZURE_ACTIVATION_REVIEW.md](AZURE_ACTIVATION_REVIEW.md). This repair does not turn
offline SDK conformance into a successful live Voice Live claim.

## Checks and costs

| Check | Result |
| --- | --- |
| Backend suite | 211 passed; 2 explicitly optional Aura skips |
| Targeted Azure/guard/MCP/embedding/voice/research/document suite | 136 passed |
| Ingestion/batching/guard regressions | 50 passed |
| Frontend tests | 33 passed |
| TypeScript/Vite build | Passed |
| Playwright | 18 passed with isolated stores and explicit simulated providers |
| Normal app browser acceptance | Passed with Azure disabled; actual local operations |
| Python dependency check | Passed |
| Scoped Ruff / ESLint / mypy | Passed |
| Git whitespace check | Passed |
| npm audit | 31 existing dependency vulnerabilities: 1 low, 4 moderate, 24 high, 2 critical |

No repository dependency versions were changed. Dependency audit debt and the
existing public-production gates (text/case authorization, retention/encryption,
hosted sign-in, abuse limits and multi-replica storage) remain. New local document
endpoints share the local application's trust boundary; do not expose this stack
as a public hosted service without authorization and retention work.

**New Azure inference cost estimate: CAD 0.** The retained cumulative application
estimate is **CAD 1.110658**, with **CAD 6.889342** estimated allowance remaining.
The local estimates and subscription alerts are not a hard invoice cap and do not
cover unrelated resources. No usage ledger was reset.

## Using the repaired application

1. Open **http://127.0.0.1:5173** and refresh app status.
2. Enter a fictional ticket and use **Save request locally**. Reopen it from Cases.
   Automated investigation remains visibly blocked until Azure readiness is met.
3. Select **PDF**, choose a text PDF/UTF-8 text document, and click **Extract text**.
4. Use **Save document locally**. This permits cited keyword retrieval immediately
   without Azure. Paste mode offers the same local saving action.
5. Enter words from the document under **Knowledge search** and inspect the returned
   source IDs and excerpts. The prior indexed VPN fixture can also be searched.
6. Semantic indexing and AI answers require available guarded Azure embeddings and
   reasoning, plus a deliberate review of the restrictive acceptance policy.
   Disabled buttons explain that dependency. They do not activate a fake fallback.
7. Leave voice and paid retries disabled in this state.

Normal startup is `make dev`; the backend owns MCP. The latest development PID file
remains `/private/tmp/deskmate-azure-activation-20261013-nn8sspiz/server-pids.json`.
Emergency shutdown remains:

```bash
./scripts/azure-emergency-stop.sh \
  /private/tmp/deskmate-azure-activation-20261013-nn8sspiz/server-pids.json
```

Private `.env` was not edited. Existing stores and semantic vectors were preserved.
Private repair logs/screenshots/results live outside Git; no credentials, database
files or test stores are part of the source commit.
