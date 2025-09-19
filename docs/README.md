# DeskMate — GraphRAG Service Desk Pilot

Near-production skeleton with FastAPI backend and React (Vite + TS) frontend.

## Prerequisites
- Python 3.10+
- Node.js 18+

## Setup
```
pip install -r requirements.txt
cd frontend && npm install
```

## Run (dev)
In one terminal:
```
uvicorn backend.app.main:app --reload --port 8000
```

In another terminal:
```
cd frontend
npm run dev
```

Open: http://localhost:5173

API health: http://localhost:8000/health → {"status":"ok"}

Ask:
```
curl -X POST http://localhost:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"Hello?"}'
```

## Build Frontend
```
cd frontend
npm run build
```
Then the backend will serve the static build at `/` from `frontend/dist` (SPA fallback to `index.html`).

## Local SLM (no API)
You can run locally without external API costs using a small local model provider.

### Option A — Ollama (recommended)
1. Install: `brew install ollama`
2. Start and prepare model: `make slm` (uses `MODEL_NAME` env, default `tinyllama`)
3. Run backend: `make dev`

Set provider via env:
```
export MODEL_PROVIDER=ollama
export MODEL_NAME=tinyllama
export MODEL_TIMEOUT_SEC=20
```

### Option B — llama.cpp server
If you have a llama.cpp server binary:
```
export LLAMACPP_BIN=./server
export MODEL_PATH=./models/your-model.gguf
scripts/start_slm.sh
export MODEL_PROVIDER=llamacpp
```

### Fallback
If no SLM is available, the service falls back to a stub that returns:
`hi, this was a test you pass`

