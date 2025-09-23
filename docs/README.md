# Service Desk Copilot

Local-first GraphRAG playground that keeps the entire retrieval-and-answering loop on your machine. A FastAPI backend coordinates ingestion, Neo4j and Chroma stores, and optional small language models, while a Vite + React frontend handles ingest and chat.

## Highlights
- Hybrid retrieval: request planner decides between graph, vector, or blended search, falling back gracefully when a store is offline.
- Ingestion endpoints for raw text and PDF files with deterministic chunking, embeddings, graph upserts, and entity linking.
- Local model providers: start with a deterministic stub and opt into Ollama or llama.cpp without touching the application code.
- Frontend chat with citations, recent-question history, and an ingest console that mirrors the backend responses.

## Architecture
- **FastAPI backend** (`backend/app`)
  - `/ingest/paste` + `/ingest/pdf` -> chunk, embed, and upsert into Chroma + Neo4j (with in-memory fallbacks).
  - `/ask` -> planner -> retriever -> responder pipeline returning answer text, citations, latency, and confidence.
  - Middleware enforces payload limits (1 MiB ask / 5 MiB ingest) and logs requests.
- **Stores**
  - Chroma keeps vector embeddings on disk under `store/chroma`.
  - Neo4j captures documents, chunks, and entities; the repo boots constraints on startup.
- **React frontend** (`frontend/src`)
  - Ingest panel for paste/PDF uploads, chat composer, citation viewer, and recent prompt list.
- **Docs**: `docs/RAG.md` contains the full ASCII data flow.

## Prerequisites
- Python 3.10+
- Node.js 18+
- Docker (needed for Neo4j; optional for Ollama)

## Setup
1. Copy environment defaults:
   ```bash
   cp .env.example .env
   ```
2. Create a virtual environment and install backend dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Install frontend dependencies:
   ```bash
   cd frontend
   npm install
   cd ..
   ```

### Environment variables
Edit `.env` or export overrides before launching. Common settings:
- `MODEL_PROVIDER`: `stub` (default), `ollama`, or `llamacpp`.
- `MODEL_NAME`: provider-specific model name (e.g. `llama3:8b`).
- `EMBED_PROVIDER`: `auto` (Ollama -> sentence-transformers -> stub) or explicit `ollama` / `sentence` / `stub`.
- `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`: connection for the graph store.
- `CHROMA_DIR`: on-disk path for the Chroma client (default `store/chroma`).
- `ALLOWED_ORIGINS`: comma-delimited CORS origins for the frontend.
- `VITE_API_BASE`: base URL the frontend uses to reach the API in development.

## Running locally
1. **Start Neo4j** (data persists under `~/Documents/service-desk-copilot/neo4j`):
   ```bash
   docker compose up -d neo4j
   ```
2. **Optional – start a local model**:
   ```bash
   bash scripts/start_slm.sh
   ```
   This script will launch `ollama serve` or a llama.cpp server when available.
3. **Run the dev servers** (FastAPI with reload + Vite dev server):
   ```bash
   source .venv/bin/activate
   make dev
   ```
4. **Open the frontend** at http://localhost:5173. The header shows the active provider reported by `/health`; when the backend is offline a banner prompts you to start it.

The backend listens on http://localhost:8000. The Vite dev server keeps its status banner visible while running.

To build the SPA for production:
```bash
cd frontend
npm run build
```
The FastAPI app serves `frontend/dist` automatically when it exists.

## Ingesting content
**UI workflow**
1. Visit http://localhost:5173.
2. Use the Ingest panel (Paste or PDF tab) to send data to the backend.
3. Successful requests display counts for chunks, entities, vectors, and (for PDFs) pages.

**CLI examples**
```bash
# Paste ingest
curl -s -X POST http://localhost:8000/ingest/paste \
  -H "Content-Type: application/json" \
  -d '{"title":"Sample Manual","text":"Widgets 101. A widget has Parts A and B."}'

# PDF ingest
curl -s -X POST http://localhost:8000/ingest/pdf \
  -F file=@sample.pdf
```

## Asking questions
```bash
curl -s -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"How does Part A relate to Part B?"}' | jq
```
Responses include the answer, citations (with document + chunk IDs and text snippets), request latency, and confidence. The frontend shows citations beneath each assistant reply; expand them to inspect the retrieved chunks.

## Optional local models
- Run `bash scripts/start_slm.sh` to detect and help start Ollama or llama.cpp servers.
- **Ollama**: `MODEL_PROVIDER=ollama`, `MODEL_NAME=llama3:8b`, ensure `ollama serve` is available at `OLLAMA_HOST`.
- **llama.cpp**: launch your server on `http://localhost:8080` (`MODEL_PROVIDER=llamacpp`).
- Leave the provider on `stub` for deterministic, dependency-free answers while developing.

## Troubleshooting
- **Backend banner complaining about reachability**: ensure `make dev` is running and `VITE_API_BASE` in `.env` points to the backend host (defaults to `http://localhost:8000`).
- **Neo4j fails to start**: verify that `~/Documents/service-desk-copilot/neo4j` exists and is writable; Docker creates the directory on the first run.
- **PDF ingest returns an error**: install `pdfminer.six` (`pip install pdfminer.six`) or run `pip install -r requirements.txt` to pull in the optional dependency.

## Tooling
- `make dev` - launch FastAPI + frontend with a shared shutdown trap.
- `make compose-up` / `make compose-down` - start or stop Docker services.
- `make ingest-sample` - seed the ingest endpoint with a canned JSON payload.
- `make fmt` - Ruff (auto-fix), Black, and Prettier across backend + frontend.
- `make test` - run the backend pytest suite (`test_ingest_integration.py` is marked slow/optional).

## Repository layout
```
.
|- backend/          # FastAPI app, RAG pipeline, stores, and tests
|- frontend/         # Vite + React TypeScript SPA
|- docs/             # Project docs, including RAG flow diagram
|- scripts/          # Dev helpers (dev server orchestration, SLM launcher, Neo4j bootstrap)
|- store/            # Runtime data (Chroma vector store, optional Ollama cache)
`- Makefile
```

## Further reading
- `docs/RAG.md` - ASCII diagram of the ingestion and retrieval pipeline.
- `agent.md` - internal notes that stay in sync with the latest sprint snapshot.
