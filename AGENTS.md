# DeskMate architecture

DeskMate is a stateful service-desk investigation assistant.

React → FastAPI `/ask` → LangGraph → bound LangChain tools → async MCP stdio client → DeskMate MCP server → capabilities.

LangGraph is the sole orchestrator. A supervisor classifies requests and invokes compiled knowledge/case subgraphs. Model-selected plans are validated against MCP-discovered schemas and read permissions, with explicit parallel groups. A separate verifier checks candidates and can trigger further research. Structured Azure outputs use the canonical `TicketAnalysis`, `InvestigationDecision`, and `InvestigationState`. Human questions use `interrupt()` and `Command(resume=...)`, with stable `thread_id=case_id` and persistent Cosmos checkpoints in production or SQLite checkpoints locally.

The lifespan owns one MCP client. Its dedicated task enters, uses and exits the SDK stdio/session contexts. LangChain tools call MCP, never repositories directly. Retrieval remains in `backend/app/rag/retrieve.py`, behind the MCP `search_knowledge` tool. Deployed production requires `DATA_PLATFORM=azure`: Cosmos DB stores tenant-partitioned cases, facts, events, voice state, jobs and LangGraph checkpoints; private Blob stores documents/artifacts; Azure AI Search supplies hybrid/semantic knowledge. Only the checkpointer uses database infrastructure directly. SQLite/Chroma and optional Aura remain available for local development and rollback. Knowledge requests are classified before case creation and do not write case records. Fictional enterprise context (`source_system=local_fixture`) is disabled in production. Public web research requires explicit workspace authorization in production.

Production validates Entra signature, audience, issuer, tenant, scope and token expiry. Signed tenant authority is captured per MCP request outside model arguments. Admin diagnostics and emergency controls require operator authorization. Voice cases and transcripts remain owner-scoped. Managed SDK work must not block the audio event loop. Preserve the emergency-disabled ledger, CAD 8 estimated allowance, CAD 10 alert budget and monthly infrastructure reservation. Never automatically retry ambiguous writes or paid retrieval after a transport failure.

Live reasoning is Azure OpenAI through Microsoft Foundry. Live embeddings are Azure `text-embedding-3-small` (384 configured dimensions). Browser speech uses Azure Voice Live through an authenticated backend WebSocket. A compiled voice LangGraph controls extraction, MCP read planning and grounded responses; deterministic code owns contact confirmation and resolution. Fake voice and stub generation/embeddings require `TEST_MODE=true` and are exclusively for isolated tests. Generation and embedding failures must be explicit.

Protect `.env`, user SQLite files and Chroma data. Never reset a collection in response to a read/write error. Separate verified embedding collections prevent mixing test and live vectors; legacy `chunks` remains intact. Exact SHA-256 content hashes provide persistent ingestion deduplication.

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
