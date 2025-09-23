# Service Desk Copilot - Agent Notes

## Sprint 2 Snapshot
- FastAPI backend runs a full hybrid GraphRAG pipeline: ingestion endpoints chunk content, embed it, and persist to Chroma (vectors) plus Neo4j (document/chunk/entity graph) with in-memory fallbacks for offline work.
- `/ask` executes planner -> retriever -> responder, returning grounded answers with citations, planner metadata, latency, and a heuristic confidence score. Stub responses stay deterministic for tests.
- React frontend exposes a dual-mode ingest panel (Paste | PDF), a chat workspace with expandable citations, and a recent-question sidebar. Threads reset on refresh.
- Tooling additions include Docker Compose for Neo4j (APOC-enabled), helper scripts for dev orchestration and local SLM startup, and Make targets for fmt/test/dev/compose/ingest-sample.

## Backend Overview
- **App wiring (`backend/app/main.py`)**
  - Configured once via `get_settings()`, sets CORS, logs requests, and enforces payload limits (1 MiB for `/ask`, 5 MiB for ingest endpoints).
  - Startup initialises a persistent `VectorChromaStore` and `GraphRepository`; if Chroma, the neo4j driver, or connectivity is unavailable the API swaps to in-memory stores and logs the fallback.
  - `/ingest/paste` validates body size, instantiates `IngestService`, and returns an `IngestPasteResponse`. `/ingest/pdf` accepts `application/pdf` or octet-stream uploads, extracts text with pdfminer, and reuses the text pipeline.
  - `/ask` builds a planner with the active graph repo, derives `top_k`, executes vector/graph/hybrid retrieval as indicated, then calls the provider-backed responder. Graph/hybrid fall back to vector search when no results are found.
  - `SpaStaticFiles` serves the built frontend (`frontend/dist`) while preserving SPA routing.
- **Configuration (`backend/app/core/config.py`)**
  - Pydantic `Settings` surface model, embedding, graph, and planner controls, normalising case and guarding positive integers. `get_settings()` ensures `logs/` and `store/chroma/` directories exist.
- **Services**
  - `chunking.py` approximates token counts and splits text based on env-driven `chunk_tokens` / `chunk_overlap` with sane bounds.
  - `entities.py` prefers spaCy (`en_core_web_sm`) when available; otherwise falls back to regex heuristics while normalising entity names.
  - `ingest_service.py` splits chunks, generates embeddings, upserts into Chroma, records documents/chunks/entities in Neo4j, links relationships, and reports elapsed time. PDF ingest counts pages via form-feed markers before delegating to text ingest.
- **Embedding providers (`adapters/embeddings.py`)**
  - Supports Ollama embeddings, sentence-transformers, and a deterministic stub. `auto` tries Ollama -> sentence-transformers -> stub and surfaces HTTP/installation errors as fallbacks.
- **RAG components (`backend/app/rag`)**
  - `Planner` extracts entities, queries graph degree, and chooses `GRAPH`, `VECTOR`, or `HYBRID` (graph threshold >=3). Sparse or empty graphs default to vector mode.
  - `Retriever` performs vector search (Chroma), graph lookups (Neo4j entity-degree filtering), or hybrid intersection; empty results drop back to vector results.
  - `Responder` composes a DeskMate prompt, calls the selected provider, and emits citations with snippets. Stub provider returns `DEFAULT_STUB_ANSWER` for hermetic testing.
- **Stores (`backend/app/store`)**
  - `VectorChromaStore` lazily creates a persistent collection and exposes `upsert`/`search`. `InMemoryVectorStore` offers a basic fallback list search.
  - `GraphRepository` bootstraps Neo4j constraints, maintains document/chunk/entity nodes, associates relationships, and handles case-insensitive entity lookups. `InMemoryGraphRepository` mirrors the interface using dict/set storage.

## Frontend Overview (`frontend/src`)
- `App.tsx` orchestrates ingest mode toggles, chat thread state, error handling, and API calls against `import.meta.env.VITE_API_BASE` (falls back to `window.location.origin`). Recent questions keep the latest five prompts.
- `Composer.tsx` auto-resizes the textarea, intercepts Enter vs Shift+Enter, and delegates to the supplied `onSend` handler.
- `MessageBubble.tsx` renders role-labelled bubbles, exposes expandable citations, and shows pending/error styling.
- `styles.css` defines the glassmorphism layout, responsive two-column grid, and UI polish for ingest and chat surfaces.

## Data, Storage, and Fallbacks
- Vector data persists under `store/chroma`; Neo4j mounts to `~/Documents/service-desk-copilot/neo4j` when run via Docker Compose.
- `logs/` captures backend and helper-script output (e.g. `scripts/start_slm.sh`).
- Missing optional dependencies (neo4j driver, spaCy model, sentence-transformers, Ollama) all degrade gracefully to in-memory stores or stub providers while logging warnings.

## Tooling & Operations
- **docker-compose.yml** spins up Neo4j 5.25.1 with APOC and health checks; an Ollama service is ready to uncomment when needed.
- **Make targets**: `make dev`, `make fmt`, `make test`, `make compose-up`, `make compose-down`, `make ingest-sample`.
- **Scripts**: `scripts/dev.sh` starts uvicorn + Vite with a cleanup trap; `scripts/start_slm.sh` detects/starts Ollama or llama.cpp servers; `scripts/neo4j/00_constraints.cypher` is ready for additional graph bootstrapping.

## Testing
- Backend unit tests live in `backend/app/tests` and cover DTOs, chunking, embeddings, entities, RAG planner/retriever/answer flows, and vector/graph adapters. `test_ingest_integration.py` is marked slow/optional for end-to-end ingest coverage.
- Use `make test` (pytest) and `make fmt` (Ruff/Black/Prettier) before pushing changes.

Keep these notes updated whenever the ingest flow, retrieval strategy, model providers, or developer tooling changes so that future prompts can rely on a single authoritative reference.
