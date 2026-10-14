# DeskMate architecture

DeskMate is a stateful service-desk investigation assistant.

React → FastAPI `/ask` → LangGraph → bound LangChain tools → async MCP stdio client → DeskMate MCP server → capabilities.

LangGraph is the sole orchestrator. A supervisor classifies requests and invokes compiled knowledge/case subgraphs. Model-selected plans are validated against MCP-discovered schemas and read permissions, with explicit parallel groups. A separate verifier checks candidates and can trigger further research. Structured Azure outputs use the canonical `TicketAnalysis`, `InvestigationDecision`, and `InvestigationState`. Human questions use `interrupt()` and `Command(resume=...)`, with stable `thread_id=case_id` and persistent tenant-scoped SQLite record checkpoints on the Mac. The older managed checkpoint format is imported without rewriting serialized state.

The lifespan owns one MCP client. Its dedicated task enters, uses and exits the SDK stdio/session contexts. LangChain tools call MCP, never repositories directly. Retrieval remains in `backend/app/rag/retrieve.py`, behind the MCP `search_knowledge` tool. The active Mac profile uses `DATA_PLATFORM=local` and `LOCAL_PERSISTENCE_ENABLED=true`: persistent Chroma holds verified knowledge/vectors, private local files retain immutable originals and extraction artifacts, and SQLite records provide transactional cases, facts, events, document jobs, voice leases, usage accounting and checkpoints. Azure OpenAI, Document Intelligence and Voice Live perform AI processing only. Legacy Cosmos/Blob/Search adapters remain available for verified export and rollback; they are not active persistence dependencies. Never reactivate a cloud writer during local cutover without reconciling new data. Only the checkpointer uses database infrastructure directly. Knowledge requests are classified before case creation and do not write case records. Fictional enterprise context (`source_system=local_fixture`) is disabled in production. Public web research requires explicit workspace authorization in production.

Production validates Entra signature, audience, issuer, tenant, scope and token expiry. Signed tenant authority is captured per MCP request outside model arguments. Admin diagnostics and emergency controls require operator authorization. Voice cases and transcripts remain owner-scoped. Managed SDK work must not block the audio event loop. The latest explicitly approved application allowance is CAD 155, including the existing CAD 90 infrastructure forecast. Preserve all financial history, persistent emergency controls and CAD 10 Azure alert budget. The Mac profile uses two-minute sessions, no daily session-count limit (`AZURE_MAX_LIVE_SESSIONS_PER_DAY=0`), and one concurrent session. Normal session completion must never disable the usage guard. Spending reservations still apply; increasing the allowance requires explicit approval. Never automatically retry ambiguous writes or paid retrieval after a transport failure. A point read may retry one transient transport failure.

Live reasoning is Azure OpenAI through Microsoft Foundry. Live embeddings are Azure `text-embedding-3-small` (384 configured dimensions). Browser speech uses Azure Voice Live through an authenticated backend WebSocket. A compiled voice LangGraph controls extraction, MCP read planning and grounded responses; deterministic code owns contact confirmation and resolution. Fake voice and stub generation/embeddings require `TEST_MODE=true` and are exclusively for isolated tests. Generation and embedding failures must be explicit.

Protect `.env`, user SQLite files and Chroma data. Never reset a collection in response to a read/write error. Separate verified embedding collections prevent mixing test and live vectors; legacy `chunks` remains intact. Migrated legacy embedding spaces are retained in separate lexical-only Chroma archive collections. Keep `store/local/` and `store/backups/` private and ignored by Git. See `docs/LOCAL_STORAGE_VOICE.md` for backup, cutover, verification and rollback. Exact SHA-256 content hashes provide persistent ingestion deduplication.

Use Python 3.12 and the repository `.venv`:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/python -m pytest backend/app/tests -q
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run test:e2e
```

Start the application with `make dev`. To run MCP independently: `PYTHONPATH=. .venv/bin/python -m backend.app.mcp.server`. FastAPI starts and closes its own MCP subprocess; a separate MCP process is unnecessary.

Tests use real temporary Chroma and SQLite stores and real MCP stdio. Mock external services explicitly in unit tests. Browser tests use real HTTP against an isolated test stack, with backend-only deterministic providers. Aura integration tests require explicit `RUN_AURA_TESTS=1` and isolated credentials. Do not write live graph data during routine tests.
