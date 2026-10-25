# DeskMate

DeskMate investigates IT service-desk problems using stateful LangGraph workflows, LangChain model/tool interfaces and dynamically discovered MCP capabilities. Recommendations retain their evidence, uncertainty and verification status. A proposed cause is not a confirmed resolution.

The Mac stores original documents and extraction artifacts in private local files, knowledge/vectors in persistent ChromaDB, and transactional cases, transcripts, checkpoints, ownership and financial history in SQLite. SentenceTransformers generates embeddings locally. Optional online Neo4j Aura supplies graph relationships; its absence leaves supported vector-only investigations available.

The top-bar selector chooses **Groq** (default) or **Azure OpenAI** for subsequent requests and persists that choice in this browser. It never silently falls back. Azure AI Speech performs continuous **Speech-to-Text only**; model responses are text. Azure Document Intelligence remains optional for extraction. Azure does not provide application persistence.

## Run locally

Use Python 3.12, Node.js 22 and the repository `.venv`:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
npm --prefix frontend ci
make dev
```

For a new installation, review `.env.example` and privately configure the required values. Preserve an existing `.env`, signing key, database and Chroma directory.

Open **http://127.0.0.1:5173**. FastAPI runs on port 8000 and owns its MCP subprocess. A separate MCP terminal is unnecessary. Stop the stack with Ctrl+C. Backend environment changes require restarting the stack; the model selector applies without restarting. LangGraph pauses for missing information and resumes the same stored conversation when you answer.

## Configure

- Groq: private `GROQ_API_KEY` and `GROQ_MODEL_NAME`. Account/model rate limits are discovered from response headers; exact optional RPM/TPD settings come from the account's current Limits page.
- Azure OpenAI: `AZURE_FOUNDRY_ENDPOINT`, `AZURE_MODEL_DEPLOYMENT` and preferably Azure CLI/Entra authentication. Provider quotas, rate limits and authentication remain in effect; DeskMate does not impose a spending ledger or fallback provider.
- Local persistence: `LOCAL_PERSISTENCE_ENABLED=true`, `CASE_DB_PATH`, `CHROMA_DIR`, `LOCAL_DOCUMENTS_DIR`; keep `store/local/` and `store/backups/` private and ignored.
- Optional graph: `NEO4J_URI`, `NEO4J_USER`, private `NEO4J_PASSWORD`, and `NEO4J_DATABASE`. The [official read-only Neo4j MCP setup](docs/NEO4J_MCP.md) adds dynamically discovered Aura evidence over a FastAPI-owned local stdio process. Unavailable Aura is reported honestly; no production graph fixtures are substituted.
- Continuous transcription: `SPEECH_ENABLED`, `AZURE_SPEECH_ENDPOINT`, language and conservative estimated CAD/hour. Use a compatible existing resource and Entra role assignments; see [Speech](docs/SPEECH.md).
- Extra MCP servers: explicitly configured `MCP_SERVERS`, with dynamic tool discovery and model read permissions. A connected descriptor does not grant destructive permissions.

## Verification

```bash
make test-fast       # changed-code development loop
make test-memory     # conversation and personal-memory acceptance
make test-mcp        # MCP contracts, transport and security
make test-ui         # React components
make test-regression # complete offline PR/release gate
```

These profiles are fail-closed against non-loopback network access and keep automated fixtures in temporary stores. They do not establish live model, Neo4j or physical microphone success. `make test-live` is separate and refuses to start without both explicit live-profile and authorization flags. Never upload normal user knowledge merely to populate a demonstration. See [Testing profiles](docs/TESTING.md) and the [test inventory](docs/TEST_INVENTORY.md).

Read the [current architecture and verification report](docs/ARCHITECTURE_SIMPLIFICATION.md), [reasoning evaluation](docs/REASONING_EVALUATION.md), [Speech behavior](docs/SPEECH.md), and [local retrieval](docs/RAG.md). Earlier Azure persistence/Voice Live reviews are historical records, not active setup instructions.

## Development and releases

DeskMate uses `dev` for active integration, `staging` for preproduction validation and `main` for production-ready releases. Temporary feature branches merge into `dev`; promotion then proceeds through pull requests from `dev` to `staging` and from `staging` to `main`. Do not push feature work directly to `main` or force-push a permanent branch.

See [Branching and deployment](docs/BRANCHING_AND_DEPLOYMENT.md) for required checks, rollback, environment isolation, branch protection and the 2026-10-23 branch reconciliation report. The repository currently provides validation gates but no configured hosting target, so workflows do not claim a live deployment.
