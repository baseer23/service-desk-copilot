# DeskMate

DeskMate investigates IT service-desk problems using stateful LangGraph workflows, LangChain model/tool interfaces and dynamically discovered MCP capabilities. Recommendations retain their evidence, uncertainty and verification status. A proposed cause is not a confirmed resolution.

The Mac stores original documents and extraction artifacts in private local files, knowledge/vectors in persistent ChromaDB, and transactional cases, transcripts, checkpoints, ownership and financial history in SQLite. SentenceTransformers generates embeddings locally. Optional online Neo4j Aura supplies graph relationships; its absence leaves supported vector-only investigations available.

The top-bar selector chooses **Groq** (default), **Azure OpenAI**, or an independently deployed **Azure OpenAI GPT-5 Nano** for subsequent requests and persists that choice in this browser. It never silently falls back. Azure AI Speech performs continuous **Speech-to-Text only**; model responses are text. Azure Document Intelligence remains optional for extraction. Azure does not provide application persistence.

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
- Azure OpenAI: `AZURE_FOUNDRY_ENDPOINT`, `AZURE_MODEL_DEPLOYMENT` and preferably Azure CLI/Entra authentication. GPT-5 Nano additionally requires its own `AZURE_GPT5_NANO_DEPLOYMENT`; it never aliases or falls back to the ordinary deployment. The model must already be available and deployed in the configured Azure resource. Regional availability, deployment type, quota and actual billing vary. Indicative October 2026 Global Standard pricing is US$0.05/M input tokens and US$0.40/M output tokens; verify the account-specific Azure price before use. Provider quotas, rate limits and authentication remain in effect; DeskMate does not impose a spending ledger or fallback provider.

For local website ingestion, set `ALLOW_URL_INGEST=true` and restart the backend. The Knowledge URL tab performs a bounded public HTTP(S) crawl, indexes readable HTML with local embeddings, and remains searchable when Neo4j is unavailable. `ENABLE_PUBLIC_WEB_TOOLS` separately controls live web research and is not required for an explicitly submitted ingestion URL. JavaScript-only pages are not rendered.
- Local persistence: `LOCAL_PERSISTENCE_ENABLED=true`, `CASE_DB_PATH`, `CHROMA_DIR`, `LOCAL_DOCUMENTS_DIR`; keep `store/local/` and `store/backups/` private and ignored.
- Optional graph: `NEO4J_URI`, `NEO4J_USER`, private `NEO4J_PASSWORD`, and `NEO4J_DATABASE`. The [official read-only Neo4j MCP setup](docs/NEO4J_MCP.md) adds dynamically discovered Aura evidence over a FastAPI-owned local stdio process. Unavailable Aura is reported honestly; no production graph fixtures are substituted.
- Continuous transcription: `SPEECH_ENABLED`, `AZURE_SPEECH_ENDPOINT`, language and conservative estimated CAD/hour. Use a compatible existing resource and Entra role assignments; see [Speech](docs/SPEECH.md).
- Extra MCP servers: explicitly configured `MCP_SERVERS`, with dynamic tool discovery and model read permissions. A connected descriptor does not grant destructive permissions.

## Verification

```bash
.venv/bin/python -m pytest backend/app/tests -q
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run test:e2e
```

Automated fixtures stay in temporary stores. They do not establish live model, Neo4j or physical microphone success. Live evaluators require explicit opt-in and respect existing spending controls. Never upload normal user knowledge merely to populate a demonstration.

Read the [current architecture and verification report](docs/ARCHITECTURE_SIMPLIFICATION.md), [reasoning evaluation](docs/REASONING_EVALUATION.md), [Speech behavior](docs/SPEECH.md), and [local retrieval](docs/RAG.md). Earlier Azure persistence/Voice Live reviews are historical records, not active setup instructions.

## Development and releases

DeskMate uses `dev` for active integration, `staging` for preproduction validation and `main` for production-ready releases. Temporary feature branches merge into `dev`; promotion then proceeds through pull requests from `dev` to `staging` and from `staging` to `main`. Do not push feature work directly to `main` or force-push a permanent branch.

See [Branching and deployment](docs/BRANCHING_AND_DEPLOYMENT.md) for required checks, rollback, environment isolation, branch protection and the 2026-10-23 branch reconciliation report. The repository currently provides validation gates but no configured hosting target, so workflows do not claim a live deployment.
