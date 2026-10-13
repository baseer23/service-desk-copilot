# MCP-first supervisor upgrade validation

Historical results below predate the Azure-only browser voice migration. They do
not establish live Azure acceptance. Current results are in [VOICE_REVIEW.md](VOICE_REVIEW.md).

Validated October 8, 2026. Changes remain uncommitted; no push was performed.

React → FastAPI → LangGraph supervisor and compiled knowledge/case subgraphs →
LangChain `StructuredTool.ainvoke()` → official MCP stdio client/server →
existing storage, enterprise fixtures and public-web capabilities.

The application process accesses only checkpoint SQLite directly. Case snapshots,
events, semantic memory, ingestion, retrieval and external context all cross MCP.
previous hosted provider proposes typed plans and decisions; discovered input schemas, permissions,
tool counts, parallel groups and evidence references are validated before use.
The independent critic reviews the current candidate and evidence, excluding
earlier critic text and preliminary hypotheses. Invalid questions return to
reasoning before a human interrupt. Unsupported final answers are withheld.

| Requested check | Result | Evidence |
|---|---|---|
| SUPERVISOR GRAPH | PASS | Conditional routing, bounded cycles, critic/finalization tests; live previous hosted provider routing. |
| KNOWLEDGE SUBGRAPH | PASS | Resume, skills and PDF tests; live uploaded-PDF answer. |
| CASE SUBGRAPH | PASS | VPN, MFA, printer and shared-outage tests; live VPN contextual investigation. |
| VERIFIER | PASS | Unsupported answers/causation rejected; irrelevant MFA location question revised before interrupt; independent review context asserted. |
| DYNAMIC TOOL SELECTION | PASS | previous hosted provider plans use MCP-discovered descriptions/schemas; tool permissions and arguments tested. |
| PARALLEL TOOL EXECUTION | PASS | Real SDK requests overlap on one owned stdio session; measured peak concurrency at least five. |
| LANGCHAIN TOOL INVOCATION | PASS | Instrumented actual `StructuredTool.ainvoke()` calls match real MCP calls. |
| MCP-ONLY CAPABILITY ACCESS | PASS | Parent-process repository calls prohibited during an instrumented investigation. |
| HUMAN INTERRUPT/RESUME | PASS | Real interrupt/Command resume, stable case/thread, restart persistence and idempotent questions. |
| CROSS-CASE MEMORY | PASS | Human/trusted facts reused across cases; timestamps and conflicting values retained. |
| NEO4J RELATIONSHIP REASONING | PASS — adapter tests | Parameterized bounded relationship discovery and fixture provenance tested. Live Aura unavailable. |
| CHROMA RAG | PASS | Real temporary persistent Chroma; live MiniLM retrieval from uploaded PDF. |
| WEB RESEARCH LOOP | PASS | Search selection → fetch → actual-content usefulness review → ingest → retrieve; irrelevant pages rejected; errors become evidence; bounded rounds. |
| RESUME TEST | PASS | Backend, real-HTTP Playwright and live previous hosted provider browser flow. |
| VPN TEST | PASS | Backend correlation/resume/memory, Playwright and live previous hosted provider site question. |
| MFA TEST | PASS — automated | Identity/context investigation; no irrelevant site question reaches the user. Additional live previous hosted provider attempt blocked by TPD quota. |
| PRINTER TEST | PASS — automated | Device/queue diagnostics and next action without a floor question. Additional live previous hosted provider attempt blocked by TPD quota. |
| E2E | PASS | Four Chromium tests against isolated real HTTP backend/MCP; deterministic model/embedding providers enabled only for tests. |

Validation commands and results:

- `.venv/bin/python -m pytest backend/app/tests -q`: **91 passed, 2 skipped**. The skipped tests require opt-in live Aura credentials.
- `npm --prefix frontend test -- --run`: **6 passed**.
- `npm --prefix frontend run build`: **PASS**, TypeScript and Vite production build.
- `cd frontend && npx playwright test`: **4 passed**.
- `.venv/bin/python -m ruff check backend/app --select F`: **PASS**.
- `.venv/bin/python -m pip check`: **PASS**.
- `git diff --check`: **PASS**.

Live browser checks used `TEST_MODE=false`, previous hosted provider `previous configured model`, real MCP
stdio and SentenceTransformers `all-MiniLM-L6-v2`. A clearly labeled fictional
PDF fixture supplied the validation graduation year; it is not a claim about
the user's actual resume. The PDF answer was verified by a separate previous hosted provider step.
The VPN case checked past incidents, user/device context, alerts and changes,
then reached a verified current-site question. MFA/printer hosted checks did
not complete because the configured model returned HTTP 429 with a TPD quota
limit. They must not be described as successful live previous hosted provider checks.

The app remains available at `http://127.0.0.1:5174`, backed by FastAPI on port
8000. The demo uses isolated stores under
`/private/tmp/deskmate-supervisor-demo-n1rpn_7c`; original user stores and `.env`
were not used for upgrade-test writes. Two verified and two current quota-blocked
cases are retained. Obsolete validation attempts and their checkpoints were
backed up before archiving them from the demo case list.

The supervisor uses a new checkpoint file alongside the original file. Old
checkpoints remain intact; cases paused in an incompatible pre-upgrade graph
cannot be resumed in the new topology. The API displays them with explicit
uncertainty and directs the technician to start a new investigation.

An additional diagnostic that would separately send a saved checkpoint to previous hosted provider
was rejected by automatic approval review for sensitive-payload concerns. No
such diagnostic payload was sent. Prompt-size inspection remained local, and
quota diagnosis came from the application's own authorized live requests.
