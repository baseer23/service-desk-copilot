# DeskMate testing profiles

All normal test commands are offline. `DESKMATE_TEST_PROFILE=offline` installs a process-level socket guard that permits loopback/Unix sockets and rejects every other destination before connection. It also forces stub generation and embeddings, disables Speech and Aura, and ignores credentials that happen to exist in `.env`.

## Commands

- `make test-fast` detects committed changes from the merge base with `origin/dev` (falling back to `dev`), plus staged, unstaged and untracked paths. It runs fatal Ruff checks on changed Python, the full Ruff policy on new Python files, mapped backend and React tests, TypeScript checks for frontend changes, and Playwright only for browser-contract changes. Unknown backend modules expand to the full offline backend suite.
- `make test-memory` runs checkpoint/conversation and semantic-memory acceptance coverage plus the React restoration tests.
- `make test-mcp` runs MCP contract, transport and security coverage.
- `make test-ui` runs React component tests only.
- `make test-regression` runs all non-live backend tests, React tests, TypeScript, the production build and the consolidated offline browser suite.
- `make test-live` is refused unless both `DESKMATE_TEST_PROFILE=live` and `DESKMATE_ALLOW_LIVE_API_TESTS=true` are explicitly supplied. It runs only tests marked `live`; it is never part of CI or another target.

The selector can be inspected without executing anything:

```bash
.venv/bin/python scripts/select_tests.py
```

Use `--base <ref>` to override comparison and `--all` to select the complete suite. Default CI uses `make test-regression` with the offline profile. Live checks must use isolated resources, bounded requests and their own authorization; merely finding credentials never enables them.

## Profile matrix

| Test category | Fast development | Offline regression | Live integration |
|---|---:|---:|---:|
| Changed-module unit tests | Yes | Yes | No |
| Related API tests | When affected | Yes | No |
| Semantic-memory tests | When affected | Yes | No |
| SQLite/Chroma integration | When affected | Yes | No |
| MCP security tests | When affected | Yes | No |
| React component tests | When affected | Yes | No |
| Playwright browser tests | Browser changes only | Yes | No |
| TypeScript/build | Frontend changes | Yes | No |
| Live Azure/Groq inference | Never | Never | Explicit opt-in |
| Live Neo4j Aura | Never | Never | Explicit opt-in |

## Safety and maintenance

Test stores must use temporary SQLite, Chroma and document directories. Real SentenceTransformers, paid document processing and external research are replaced with deterministic fixtures unless the test contract is specifically the external integration and the live profile is authorized. Local FastAPI, Vite, MCP stdio and loopback HTTP remain available.

Ruff's historical repository backlog is not a release gate. New Python files should be checked directly; changed frontend code uses TypeScript checks. The full build belongs to offline regression, not every backend edit.

No timing comparison was produced for the 2026-10-25 refactor because the user explicitly requested no further test execution. Historical full-suite counts are retained in the architecture/evaluation records; future runs should capture `pytest --durations=25` and the Playwright HTML/JSON report before making additional consolidation decisions.
