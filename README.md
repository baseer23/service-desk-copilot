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

For a new installation, review `.env.example` and privately configure the required values. Preserve an existing `.env`, signing key, database, Chroma directory and usage ledger. Never replace an existing ledger with an empty store to enable paid usage.

Open **http://127.0.0.1:5173**. FastAPI runs on port 8000 and owns its MCP subprocess. A separate MCP terminal is unnecessary. Stop the stack with Ctrl+C. Backend environment changes require restarting the stack; the model selector applies without restarting. LangGraph pauses for missing information and resumes the same stored conversation when you answer.

## Configure

- Groq: private `GROQ_API_KEY` and `GROQ_MODEL_NAME`. Account/model rate limits are discovered from response headers; exact optional RPM/TPD settings come from the account's current Limits page.
- Azure OpenAI: `AZURE_FOUNDRY_ENDPOINT`, `AZURE_MODEL_DEPLOYMENT` and preferably Azure CLI/Entra authentication. The finite usage allowance and persistent emergency switch remain binding.
- Local persistence: `LOCAL_PERSISTENCE_ENABLED=true`, `CASE_DB_PATH`, `CHROMA_DIR`, `LOCAL_DOCUMENTS_DIR`; keep `store/local/` and `store/backups/` private and ignored.
- Optional graph: `NEO4J_URI`, `NEO4J_USER`, private `NEO4J_PASSWORD`. Unavailable Aura is reported honestly; no production graph fixtures are substituted.
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
