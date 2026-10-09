# DeskMate architecture

DeskMate is a stateful service-desk investigation assistant.

React → FastAPI Case API → LangGraph → bound LangChain tools → async MCP stdio client → DeskMate MCP server → capabilities.

LangGraph is the sole orchestrator. Structured Groq outputs use the canonical `TicketAnalysis`, `InvestigationDecision`, and `InvestigationState`. Human questions use `interrupt()` and `Command(resume=...)`, with stable `thread_id=case_id` and SQLite checkpoints that survive restart.

The lifespan owns one MCP client. Its dedicated task enters, uses and exits the SDK stdio/session contexts. LangChain tools call MCP, never repositories directly. Retrieval remains in `backend/app/rag/retrieve.py`, behind the MCP `search_knowledge` tool. Chroma stores semantic knowledge; optional Neo4j Aura enriches retrieval. SQLite stores cases, confirmed facts and actual events. Enterprise context is an explicitly labeled B99 fixture dataset (`source_system=local_fixture`). Public web content is untrusted evidence.

Live generation is Groq. Live embeddings are SentenceTransformers `all-MiniLM-L6-v2` (384 dimensions). Stub generation/embeddings require `TEST_MODE=true` and are exclusively for isolated tests. Generation and embedding failures must be explicit.

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
