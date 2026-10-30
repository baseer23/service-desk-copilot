@AGENTS.md

# Working safely in this repo

DeskMate is a service-desk investigation assistant: React frontend, FastAPI + LangGraph backend, local SQLite/Chroma storage, optional Neo4j Aura via the official `neo4j-mcp` stdio server. Codex also works here; AGENTS.md is the shared rulebook.

- Run the app with `make dev` (http://127.0.0.1:5173). The developer's dev server on port 8000 uses real data: never send requests to it from tests or scripts without asking.
- Never call Azure, Groq or Neo4j Aura (live scripts, `--live` flags, `make test-live`) without explicit approval. They cost money and touch real data.
- Ask before running tests. Use the offline profile (`make test-fast`, `make test-regression`), which excludes `live` tests. Live tests such as `backend/app/tests/test_document_graph_chat_e2e.py` (uploads to the real running server) are `@pytest.mark.live` and skip unless `DESKMATE_TEST_PROFILE=live` and `DESKMATE_ALLOW_LIVE_API_TESTS=true`; never set those without approval.
- `pyproject.toml` is the only pytest config; register new markers there.
- Never delete data under `store/`, `data/` or `logs/`, and never commit `.env`, databases, Chroma or `store/local`/`store/backups`.
- Don't push, merge, force-push or delete branches without asking. Commit Codex's uncommitted work to a branch before changing it.
- neo4j-mcp 1.6.x uses `NEO4J_MCP_*`; `NEO4J_*` are deprecated aliases (check with `neo4j-mcp --help`).
