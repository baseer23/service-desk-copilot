# Service Desk Copilot

Local-first GraphRAG playground built with FastAPI, Neo4j, Chroma, and a Vite + React frontend. Sprint 2 adds ingestion, retrieval planning, hybrid graph/vector search, and optional local SLM providers.

## Prerequisites
- Python 3.10+
- Node.js 18+
- Docker (for Neo4j, optional Ollama container)

## Environment variables
Copy `.env.example` to `.env` and tweak as needed. Key settings:

- `MODEL_PROVIDER`: `stub` (default), `ollama`, or `llamacpp`
- `EMBED_PROVIDER`: `auto` (tries Ollama → sentence-transformers → stub) or force `sentence`/`ollama`/`stub`
- `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`: Neo4j connection (defaults to local Docker)
- `CHROMA_DIR`: file-backed Chroma directory (default `./store/chroma`)
- `VITE_API_BASE`: backend URL for the dev frontend (default `http://localhost:8000`)

## Quick start
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm install && cd ..

# start Neo4j locally (recommended)
docker compose up -d neo4j

# (optional) start Ollama container once you uncomment it in docker-compose.yml
# docker compose up -d ollama
```

## Running the app
```bash
source .venv/bin/activate
export MODEL_PROVIDER=stub                # or ollama | llamacpp
export EMBED_PROVIDER=auto
export OLLAMA_EMBED_MODEL=nomic-embed-text
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=neo4j
export CHROMA_DIR=./store/chroma
export VITE_API_BASE=http://localhost:8000
make dev
```

- Backend: http://localhost:8000
- Frontend: http://localhost:5173 (auto-opens with Vite instructions)

## Ingesting content
UI:
1. Open http://localhost:5173
2. Use the **Ingest** panel (Paste or PDF) to upload content.
3. Successful ingest shows `Chunks / Entities / Vectors` counts.

CLI sample:
```bash
curl -s -X POST http://localhost:8000/ingest/paste \
  -H "Content-Type: application/json" \
  -d '{"title":"Sample Manual","text":"Widgets 101. A widget has Parts A and B."}'
```

PDF ingest uses multipart form-data:
```bash
curl -s -X POST http://localhost:8000/ingest/pdf \
  -F file=@sample.pdf
```

## Asking questions
```bash
curl -s -X POST http://localhost:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"How does Part A relate to Part B?"}' | jq
```
Response includes `answer`, `citations`, planner metadata, latency, and confidence. Citations display under assistant messages in the UI; click to reveal the snippet text.

## Optional local SLMs
- **Ollama**: `ollama serve`, pull a model (e.g. `ollama pull llama3:8b`), set `MODEL_PROVIDER=ollama`, `MODEL_NAME=llama3:8b`.
- **llama.cpp**: run your server on `http://localhost:8080`, export `MODEL_PROVIDER=llamacpp`.
- When no model is available, leave `MODEL_PROVIDER=stub`; the pipeline still runs end-to-end with deterministic answers.

## Make targets
- `make dev` — run FastAPI (reload) + Vite.
- `make test` — backend pytest suite.
- `make fmt` — Ruff fix + Black + Prettier.
- `make compose-up` / `make compose-down` — manage Docker services.
- `make ingest-sample` — curl a sample JSON payload into the ingest endpoint.

## Building the frontend
```bash
cd frontend
npm run build
```
The backend will serve the compiled SPA from `frontend/dist` when available.
