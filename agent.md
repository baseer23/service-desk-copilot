# Service Desk Copilot – Full Snapshot (Sprint 2)

This document is the authoritative, self-contained map of the codebase. It enumerates every file, explains how pieces connect, and records the defaults, fallbacks, and operational contracts so another engineer (or model) can rebuild the repository from scratch.

## 1. Repository Layout
```
.
├─ LICENSE                                  # Proprietary notice
├─ Makefile                                  # make dev|slm|fmt|test|compose-*|ingest-sample
├─ agent.md                                  # You are here (repo snapshot)
├─ docker-compose.yml                        # Neo4j (APOC-enabled) service definition
├─ requirements.txt                          # Backend + tooling Python deps
├─ docs/
│  ├─ README.md                              # High-level project overview & setup
│  └─ RAG.md                                 # ASCII diagram of ingestion + retrieval flow
├─ logs/
│  └─ .gitkeep                               # Ensures logs/ exists in git
├─ scripts/
│  ├─ dev.sh                                 # Launch uvicorn + Vite with cleanup trap
│  ├─ start_slm.sh                           # Helper to start Ollama or llama.cpp locally
│  └─ neo4j/
│     └─ 00_constraints.cypher               # Neo4j schema bootstrap (constraints/index)
├─ backend/
│  ├─ __init__.py
│  ├─ logging.ini                            # Rotating file + console logging config
│  └─ app/
│     ├─ __init__.py
│     ├─ api/
│     │  └─ __init__.py                      # Reserved namespace for future routers
│     ├─ adapters/
│     │  ├─ __init__.py
│     │  └─ embeddings.py                    # Embedding provider implementations + factory
│     ├─ core/
│     │  ├─ __init__.py
│     │  └─ config.py                        # Pydantic Settings + defaults + cache helpers
│     ├─ models/
│     │  ├─ __init__.py
│     │  ├─ dto.py                           # Pydantic request/response models
│     │  ├─ provider.py                      # LocalModelProvider ABC
│     │  ├─ provider_factory.py              # get_provider() dispatcher (stub/ollama/llama.cpp)
│     │  ├─ provider_llamacpp.py             # llama.cpp REST client with fallbacks
│     │  ├─ provider_ollama.py               # Ollama REST client with fallbacks
│     │  └─ provider_stub.py                 # Deterministic stub provider (DEFAULT_STUB_ANSWER)
│     ├─ rag/
│     │  ├─ __init__.py
│     │  ├─ answer.py                        # Responder -> prompt + provider call + citations
│     │  ├─ planner.py                       # Planner -> choose VECTOR/GRAPH/HYBRID via entity degree
│     │  └─ retrieve.py                      # Retriever -> vector/graph/hybrid search pipeline
│     ├─ services/
│     │  ├─ __init__.py
│     │  ├─ chunking.py                      # Token approximation + chunk splitter
│     │  ├─ entities.py                      # spaCy-or-regex entity extraction
│     │  └─ ingest_service.py                # Chunk/embed/upsert pipeline (text + PDF)
│     ├─ store/
│     │  ├─ __init__.py
│     │  ├─ graph_repo.py                    # Neo4j repository + in-memory fallback
│     │  └─ vector_chroma.py                 # Chroma persistent store + in-memory fallback
│     ├─ tests/
│     │  ├─ test_answer.py                   # Responder citations & stub behaviour
│     │  ├─ test_chunking.py                 # Chunk splitter heuristics & env overrides
│     │  ├─ test_dto.py                      # Pydantic schema validation + env-driven top_k
│     │  ├─ test_embeddings.py               # Stub embeddings determinism
│     │  ├─ test_entities.py                 # Regex-driven entity extraction
│     │  ├─ test_graph_repo.py               # Neo4j repository query execution contracts
│     │  ├─ test_ingest_integration.py       # Slow e2e ingest against real Neo4j (optional)
│     │  ├─ test_ingest_service.py           # IngestService chunk/entity/vector bookkeeping
│     │  ├─ test_planner.py                  # Planner mode selection thresholds
│     │  ├─ test_provider.py                 # FastAPI health + /ask payload guards
│     │  └─ test_retrieve.py                 # Retriever vector/graph/hybrid orchestration
│     └─ main.py                             # FastAPI app wiring, middleware, endpoints
├─ frontend/
│  ├─ README.md                              # SPA setup + features
│  ├─ index.html                             # Root HTML shell (loads /src/main.tsx)
│  ├─ package-lock.json                      # Pinned npm dependency graph
│  ├─ package.json                           # Frontend scripts + deps
│  ├─ tsconfig.json                          # TS compiler config (strict)
│  ├─ vite.config.ts                         # Vite + React plugin, dev/preview port 5173
│  └─ src/
│     ├─ App.tsx                             # UI state machine (ingest + chat + sidebar)
│     ├─ components/
│     │  ├─ Composer.tsx                     # Autosizing textarea + Enter handling
│     │  └─ MessageBubble.tsx                # Role bubbles, citation toggle
│     ├─ main.tsx                            # React.StrictMode renderer
│     └─ styles.css                          # Glassmorphism layout styles
└─ store/                                    # Created at runtime (Chroma vectors, etc.)
```

## 2. Backend (FastAPI + GraphRAG)

### Provider Matrix
| Provider | Text Gen Endpoint | Embeddings | Default |
| --- | --- | --- | --- |
| stub | n/a (deterministic text) | Stub embedding provider | ✅ |
| ollama | `POST {OLLAMA_HOST}/api/generate` | `POST {OLLAMA_HOST}/api/embeddings` (falls back to sentence-transformers, then stub) |  |
| llamacpp | `POST {LLAMACPP_HOST}/completion` | Sentence-transformers (falls back to stub) |  |

### 2.1 Application entry (`backend/app/main.py`)
- Initializes logging from `backend/logging.ini`, falls back to `logging.basicConfig` when missing.
- Global constants: `MAX_BODY_BYTES = 1 MiB` for `/ask` and `MAX_INGEST_BYTES = 5 MiB` for ingest endpoints.
- On import, builds the `FastAPI` instance, applies CORS (from `Settings.allowed_origins`), and seeds `app.state` with in-memory vector/graph stores plus the current provider returned by `get_provider(SETTINGS)`.
- Startup handler refreshes settings (`get_settings()`), reinitialises the vector store (`VectorChromaStore` → `InMemoryVectorStore` fallback), graph repo (`GraphRepository` with constraint bootstrap → `InMemoryGraphRepository` fallback), and provider, storing the active Neo4j driver on `app.state.graph_driver` for shutdown.
- Shutdown handler closes the Neo4j driver when present.
- Middleware:
  - `enforce_body_limit` gates POST sizes on `/ask` and `/ingest/paste`. Checks `Content-Length` if provided; otherwise buffers body once and re-attaches it (`request._body`). Returns HTTP 413 on overflow.
  - `log_requests` logs `<METHOD> <PATH> -> <status>` for every response.
- Endpoints:
  - `GET /health`: returns the provider reported by the active adapter plus reachability booleans (`ollama_reachable`, `llamacpp_reachable`, `neo4j_reachable`) and vector store path metadata (`vector_store_path`, `vector_store_path_exists`). Failures never raise—probes are wrapped in try/except.
  - `POST /ingest/paste` (response model `IngestPasteResponse`): rejects payload over 5 MiB, builds `IngestService` with `_safe_embedding_provider` (falls back to `StubEmbeddingProvider` with a warning) and the live vector/graph stores. Calls `ingest_text(title, text)`.
  - `POST /ingest/pdf` (response model `IngestPdfResponse`): accepts `application/pdf` or `application/octet-stream`; size-protects; reads binary, feeds to `IngestService.ingest_pdf`. Wraps pdfminer errors in HTTP 400 with message.
  - `POST /ask` (response model `AskResponse`):
    - Builds `Planner` with the active graph repo to compute plan metadata.
    - Chooses `top_k`: payload `top_k` > plan `top_k` > `Settings.top_k`.
    - Instantiates `Retriever` with the current stores + embedding provider.
    - Branches on plan mode: `GRAPH` -> graph search fallback to vector; `HYBRID` -> `Retriever.hybrid_search`; default -> vector search.
    - `Responder` uses the provider stored on `app.state` (stub/Ollama/llama.cpp). When the provider raises, the response body is prefixed with `“Local model unavailable; falling back to stub. …”` and the deterministic stub answer.
- SPA mounting: if `frontend/dist` exists at runtime, mounts `/` to `SpaStaticFiles`. Custom subclass returns `index.html` fallback for unknown paths, enabling client-side routing.

### 2.2 Configuration (`backend/app/core/config.py`)
- `Settings` extends `BaseSettings` with `.env` support and default values:
  - `app_name`, `allowed_origins` (comma string parsed to list), log directory, provider options (`model_provider`, `model_name=llama3:8b`, `model_timeout_sec`), embedding options, and host URLs (`ollama_host=http://localhost:11434`, `llamacpp_host=http://localhost:8080`).
  - Graph defaults: `neo4j_uri=bolt://localhost:7687`, credentials `neo4j/neo4j` (aligns with docker-compose `neo4j:5.25.1`), `chroma_dir=store/chroma`.
  - RAG controls: `top_k=6`, `chunk_tokens=512`, `chunk_overlap=64`.
- Validators enforce lowercase provider names and non-negative/positive numeric constraints.
- `get_settings()` is `@lru_cache`d; ensures `logs/` and `store/chroma/` directories exist on each call.
- `reload_settings()` clears the cache (used by tests).
- `DEFAULT_STUB_ANSWER = "hi, this was a test you pass"` consumed by stub responders.

### 2.3 Embeddings (`backend/app/adapters/embeddings.py`)
- Defines a `Protocol` `EmbeddingProvider` with `embed_texts` contract.
- Providers:
  - `OllamaEmbeddingProvider`: POSTs to `<host>/api/embeddings`, expects `data` array or single `embedding`. Raises `RuntimeError` on HTTP error or malformed payload.
  - `SentenceTransformersEmbeddingProvider`: loads configurable sentence-transformers model; returns list of float vectors.
  - `StubEmbeddingProvider`: deterministic hash-backed pseudo-random vectors (defaults dim=384) normalized to unit length.
- Factory `get_embedding_provider(settings)` supports providers: `ollama`, `sentence`, `stub`, `auto` (tries `_ollama_available` ping -> SentenceTransformer -> Stub). Raises `ValueError` for unknown provider strings.

### 2.4 Services (`backend/app/services`)
- `chunking.py`:
  - `approx_tokens(text)`: heuristics mixing word and char counts (char/4) for stable token approximation.
  - `split_text(text, chunk_tokens=None, overlap=None)`: tokenizes on whitespace; chunk size/overlap read from args or env vars `CHUNK_TOKENS`, `CHUNK_OVERLAP` (via `_read_int_env`). Ensures sensible bounds (chunk > 0, overlap >=0 < chunk). Returns list of dicts with `id`, `ord`, `text`, `tokens`.
- `entities.py`:
  - Attempts to load spaCy `en_core_web_sm` at import; on failure uses regex fallback.
  - `extract_entities(chunks)`: concatenates chunk text; spaCy path collects entity text + noun chunks. Regex path matches capitalized phrases, stores all suffixes, and adds alphabetic words >=4 chars. Returns sorted, deduplicated, lowercased entity list.
- `ingest_service.py` (`IngestService` dataclass):
  - Expects `settings`, `vector_store`, `graph_repo`, `embedding_provider` to implement required methods.
  - `ingest_text(title, text)`:
    - Splits text to chunks using settings chunk parameters. If no chunks, returns zero-count `IngestPasteResponse` immediately (latency measured).
    - Generates a `doc_id = uuid4().hex` and per-chunk `chunk_id` (`{doc_id}-{ord}`) with metadata `doc_id` + `ord`.
    - Calls `embedding_provider.embed_texts(chunk_texts)`; couples embeddings with chunk metadata and `vector_store.upsert(records)`.
    - Writes document and chunk nodes via `graph_repo.upsert_document`, `upsert_chunk`, `link_doc_chunk` with token counts from `approx_tokens`.
    - Extracts entities from chunks; upserts entities, then links chunk→entity for any chunk where entity substring appears (`entity in chunk['text'].lower()`).
    - Returns `IngestPasteResponse` capturing chunk/entity/vector counts and elapsed ms.
  - `ingest_pdf(title, data)`:
    - Uses pdfminer `extract_text` on BytesIO; counts form-feed (`\f`) occurrences to estimate pages.
    - Delegates to `ingest_text`, wrapping counts in `IngestPdfResponse` with total latency.

### 2.5 Retrieval & Answer (`backend/app/rag`)
- `Planner` (`planner.py`):
  - `plan(question)` extracts question entities, asks `graph_repo.get_entity_degrees`.
  - If no entities or all degrees zero → mode `VECTOR` with reason.
  - Otherwise determines `max_degree`. `GRAPH` when `max_degree >= GRAPH_THRESHOLD (3)`, else `HYBRID` with reason `graph is sparse`.
  - Returns dict with `mode`, `reasons`, `top_k`, `entities` (non-zero degree names).
- `Retriever` (`retrieve.py`):
  - `vector_search`: embed question text and call `vector_store.search(embedding, top_k)`.
  - `graph_search`: extract entities, filter to positive-degree names, fetch chunks via `graph_repo.fetch_chunks_for_entities(relevant, top_k)`.
  - `hybrid_search`: run `graph_search`; if empty fallback to vector; else perform vector search and filter to chunk IDs intersecting graph results (`allowed_ids`), returning filtered list or full vector results if filter empty.
- `Responder` (`answer.py`):
  - `PROMPT_TEMPLATE` instructs DeskMate persona to answer using context with `[doc_id:chunk_id]` citations.
  - `answer(question, planner, chunks)` builds context string enumerating `[index] (title or doc_id)` lines. If settings `model_provider == 'stub'` returns `DEFAULT_STUB_ANSWER`; otherwise delegates to provider `generate(prompt)`.
  - Builds `Citation` models using chunk metadata (`doc_id`, `title`, `score`, snippet `text`).
  - Confidence heuristic: `1 / (1 + mean_score)` clamped [0.1, 0.99]; defaults to 0.5 with no scores.
  - Populates `AskResponse` with latency, planner metadata, provider name, citations.

### 2.6 Persistence Stores (`backend/app/store`)
- `vector_chroma.py`:
  - `VectorChromaStore`: ensures directory exists, instantiates `chromadb.PersistentClient` (telemetry disabled). `upsert` accepts chunk dicts (id, text, metadata, embedding). `search` queries using embedding and returns list of dicts containing id, text, metadata, score (distance).
  - `InMemoryVectorStore`: dictionary keyed by chunk id; `search` returns first `top_k` chunks (deterministic fallback).
- `graph_repo.py`:
  - `GraphRepository` wraps Neo4j driver `execute_write/read` (falls back to manual session for old drivers). Provides methods to upsert documents/chunks/entities, create relationships (`HAS_CHUNK`, dynamic `rel` sanitized via `_safe_rel`), compute entity degrees, and fetch chunk metadata for entity sets.
  - `InMemoryGraphRepository`: dictionaries of documents/chunks/entities sets; minimal operations to mimic interface, degrees based on linked chunk counts, fetch returns stored chunk snapshots.

### 2.7 DTOs & Providers (`backend/app/models`)
- `dto.py`: Pydantic models for ingest requests/responses, `Citation`, `AskRequest`, and `AskResponse`. `IngestPasteResponse` / `IngestPdfResponse` expose both legacy fields (`chunks`, `entities`, `ms`) and computed aliases (`chunks_ingested`, `entities_linked`, `latency_ms`, `pages_ingested`, etc.). `AskRequest.top_k` validator honors explicit payload, then `TOP_K` env var if positive.
- `provider.py`: `LocalModelProvider` protocol with `name() -> str` and `generate(prompt) -> str` methods.
- `provider_stub.py`: returns `DEFAULT_STUB_ANSWER` for deterministic results/testing and reports `name()='stub'`.
- `provider_ollama.py`: HTTP POST to `{OLLAMA_HOST}/api/generate`, strict JSON parsing; raises `RuntimeError` on non-200 or malformed payloads so the responder can warn and fall back.
- `provider_llamacpp.py`: HTTP POST to `{LLAMACPP_HOST}/completion`, supports common response shapes (`content`, `text`, OpenAI-like `choices`). Raises `RuntimeError` when the request or payload fails.
- `provider_factory.py`: `get_provider(settings: Settings)` dispatches to the correct adapter (ollama / llama.cpp / stub) honouring `settings.model_timeout_sec` and host URLs.

### 2.8 Logging
- `backend/logging.ini` configures root + `service-desk` logger to use rotating file handler `logs/app.log` (1 MiB, 3 backups) plus console STDOUT handler with uniform formatter `%Y-%m-%d ...`.

## 3. Frontend (Vite + React + TypeScript)

- Central React component that coordinates ingest UI, chat thread, and sidebar state.
- Constants: `API_BASE = import.meta.env.VITE_API_BASE ?? window.location.origin`.
- Defines typed responses `AskResponse`, `IngestResult`, and `HealthResponse` to mirror backend schemas.
- Local state slices: messages array (`Message` type with pending/error flags + citations), ingest mode toggle (`paste` or `pdf`), paste form fields, selected PDF `File`, ingest status and error, loading spinner, recent questions (last five unique), health status/error banner, and a reference to the scroll container.
- `useEffect` issues a one-shot fetch to `/health`; on success it surfaces the provider and reachability flags, on failure it sets a banner (`Backend <API_BASE> not reachable — run make dev`). The header renders a pill summarising the provider name.
- `postJSON` helper handles fetch, raising `Error(detail)` on non-200 responses.
- `handleAsk` pipeline: append user message + placeholder assistant bubble, update recent questions, set loading, call `/ask` with `question`. On success patch placeholder with full answer text, citations, metadata (planner, latency, confidence). On failure, show error bubble instructing to ensure backend is running. Always scroll to bottom.
- `handlePasteIngest` posts to `/ingest/paste` with title fallback (`Untitled Paste`), resets paste text, surfaces error messages. `handlePdfIngest` builds `FormData`, posts to `/ingest/pdf`, resets file on success.
- Layout renders ingest panel (tabs, forms, ingest results, errors), main chat panel (thread + composer), sidebar (recent questions list and last assistant answer card). Buttons disable while `loading`.

### 3.2 Components
- `Composer.tsx`: Controlled textarea with auto-height adjustments via `useEffect`. Intercepts Enter vs Shift+Enter to call `onSend` (awaited). Resets input and refocuses after send.
- `MessageBubble.tsx`: Renders message bubble with class modifiers for role/pending/error. For assistant messages, toggles citations list with `showCitations` state, listing `[doc_id:chunk_id] Title` and snippet when available.

### 3.3 Bootstrapping & Styling
- `main.tsx`: Standard React 18 `createRoot` + `React.StrictMode` entry.
- `styles.css`: Defines glassmorphism theme, layout grid, responsive breakpoints, bubble styling, composer, sidebar, etc. Uses CSS variables for colors, responsive adjustments under 920px (single-column) and 640px (mobile).
- `index.html`: Loads Google Fonts (Inter), sets root `<div id="root">`, includes module script `/src/main.tsx`.
- Tooling config: `vite.config.ts` adds React plugin, sets dev/preview port 5173; `tsconfig.json` uses strict compiler options, bundler module resolution.

### 3.4 Frontend Tooling
- `package.json`: Scripts `npm run dev|build|preview`; dependencies limited to React 18; dev-deps include TypeScript 5, Vite 5, Prettier, React type defs.
- `package-lock.json`: Full dependency tree (do not edit manually, keep in sync with npm install).

## 4. Tests (`backend/app/tests`)
- Suite exercises schema validation, chunking heuristics, entity extraction, store adapters, planner/retriever/responder orchestration, FastAPI endpoints, and ingest pipeline (unit + integration).
- New coverage:
  - `test_provider_paths.py` mocks Ollama/llama.cpp happy paths and failure fallbacks, asserting the responder prefixes the stub message when a provider fails.
  - `test_health_reachability.py` patches probe helpers to ensure `/health` emits provider + reachability booleans and vector store path metadata.
  - `test_ask_end_to_end_stub.py` performs ingest + ask using in-memory repositories and expects citations/confidence/latency to surface.
  - `test_retrieval_integration_minimal.py` drives a real `VectorChromaStore` (stubbed in CI) plus the in-memory graph repo to assert hybrid filtering.
  - `test_ingest_pdf_guard.py` xfails gracefully when `pdfminer.six` is unavailable.
- Existing tests cover provider payload guards, planner logic, retriever behaviour, DTO validation, and integration with optional Neo4j (`pytest.mark.slow` skipping automatically when env/driver missing).
- Run `make test` (or `pytest backend/app/tests`).

## 5. Dev Tooling & Operations
- **Makefile targets**:
  - `make dev`: delegates to `scripts/dev.sh` (runs uvicorn reload + `npm run dev` with shared trap; `LOG_DIR` env supported).
  - `make slm`: executes `scripts/start_slm.sh` to spin up Ollama/llama.cpp helpers.
  - `make fmt`: Ruff (`--fix`) + Black on backend; Prettier on frontend.
  - `make test`: Pytest suite.
  - `make compose-up/down`: manage Docker Compose Neo4j service.
  - `make ingest-sample`: Curl helper to seed sample ingest payload.
- **scripts/start_slm.sh**: Auto-detects `ollama` CLI; starts `ollama serve` if not running (logs to `logs/ollama.log`); if `LLAMACPP_BIN` set and executable plus `MODEL_PATH`, launches llama.cpp server on port 8080 (logs to `logs/llamacpp.log`). Emits guidance when neither provider available.
- **docker-compose.yml**: Only Neo4j service enabled by default (ports 7474/7687, heaps 512-1024 MiB, APOC plugin, healthcheck). Binds host state to `~/Documents/service-desk-copilot/neo4j/{data,logs,plugins}`. Ollama service provided as commented template.

## 6. Data, Logs, and Runtime Directories
- `logs/`: Created automatically by `get_settings()` and scripts. Contains rotating `app.log` plus optional `ollama.log` / `llamacpp.log` from helper scripts.
- `store/chroma`: Ensured on settings load; Chroma persists vector collections here. Delete to reset vector store.
- `~/Documents/service-desk-copilot/neo4j`: Populated when running Docker Neo4j (matching Compose volume mount).
- `frontend/dist`: Built assets appear here after `npm run build`; FastAPI serves it when present.

## 7. Configuration & Environment
- `.env` (not tracked) should define overrides:
  - `MODEL_PROVIDER` (`stub`, `ollama`, `llamacpp`), `MODEL_NAME` (default `llama3:8b`), `MODEL_TIMEOUT_SEC`.
  - `EMBED_PROVIDER` (`auto`, `ollama`, `sentence`, `stub`), `OLLAMA_EMBED_MODEL`, `OLLAMA_HOST`, `LLAMACPP_HOST`.
  - Graph + vector: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `CHROMA_DIR`.
  - CORS: `ALLOWED_ORIGINS` (comma list).
  - Planner: `TOP_K`, `CHUNK_TOKENS`, `CHUNK_OVERLAP` (validated to positive/zero).
  - Frontend dev: `VITE_API_BASE`.
- `requirements.txt` ties backend to FastAPI 0.111+, uvicorn reload, pydantic v2, requests/httpx, pytest, Ruff/Black, ChromaDB 0.5+, sentence-transformers, numpy/scikit-learn, neo4j 5.x, pdfminer.six, python-dotenv.

## 8. Runtime Behaviour Summary
1. **Ingest Paste/PDF**
   - FastAPI validates size → `IngestService` splits text, hashes embeddings (stub/HF/Ollama), stores vectors + graph nodes, extracts entities.
   - Responses include chunk/entity/vector counts and wall-clock latency (ms), plus PDF page counts when applicable.
2. **Ask**
   - Planner inspects question for entities (spaCy when installed, regex fallback otherwise).
   - Graph repo returns degrees; planner selects `VECTOR`, `GRAPH`, or `HYBRID`.
   - Retriever executes chosen strategy: vector search uses Chroma; graph search fetches chunks linked to entities; hybrid intersects vector results with graph results.
   - Responder formats context, prompts provider (stub or external LLM) and attaches citations. Confidence derived from vector distances.
3. **Fallbacks**
   - Missing Chroma path: warns and uses in-memory vector store (no persistence, simple list search).
   - Missing Neo4j driver or connectivity: warns and uses in-memory graph store (dict-backed, functional for tests/dev but ephemeral).
   - Missing embeddings provider: `_safe_embedding_provider` logs warning and falls back to deterministic stub embeddings.
   - Provider HTTP errors/timeouts: Ollama/llama.cpp adapters raise `RuntimeError`; the responder logs the failure and returns the stub answer prefixed with `"Local model unavailable; falling back to stub."` while still reporting the original provider.
   - PDF extraction issues: bubbled as HTTP 400 with reason.

## 9. Rebuilding the Project
- **Backend**
  1. Create virtualenv, install `requirements.txt`.
  2. Provide `.env` with desired providers; stub works out of the box.
  3. Run `uvicorn backend.app.main:app --reload` (or `make dev`).
- **Frontend**
  1. `cd frontend && npm install`.
  2. `npm run dev` (port 5173) or `npm run build` for production bundle (served by FastAPI).
- **Neo4j** (optional but recommended for full GraphRAG): `docker compose up -d neo4j`, credentials `neo4j/neo4j` unless overridden.
- **Chroma**: auto-initialises. Delete `store/chroma` to clear vectors.

## 10. Reference Constants & Interfaces
- `DEFAULT_STUB_ANSWER = "hi, this was a test you pass"` – consistent across backend tests and stub responses.
- Middleware enforced limits: `/ask` 1,048,576 bytes; ingest endpoints 5,242,880 bytes.
- Planner threshold: `GRAPH_THRESHOLD = 3` (entity degree >= 3 → Graph mode).
- `Citation` model: `doc_id`, `chunk_id`, `score ≥ 0`, optional `title`, `snippet`.
- Vector record schema (Chroma): `{id: str, text: str, metadata: dict(doc_id, ord, title?), embedding: List[float]}`.
- Graph relationships:
  - `MERGE (d:Document {id})`
  - `MERGE (c:Chunk {id})`, `SET` ord/text/tokens
  - `MERGE (d)-[:HAS_CHUNK]->(c)`
  - `MERGE (e:Entity {id: lower(name)})`, `SET name/type`
  - `MERGE (c)-[:ABOUT]->(e)` (rel sanitized by `_safe_rel`).

## 11. Testing & Quality Gates
- Run `make fmt` to auto-format (Ruff fix, Black, Prettier).
- Run `make test` for backend tests; note integration test may skip without Neo4j env.
- Frontend currently lacks automated tests; rely on manual QA or extend with e.g. Vitest if needed.
- Acceptance checks:
  - `/health` always responds with provider + reachability booleans and vector store path info (no crashes when services are down).
  - Provider adapters raise on network failures; the responder prefixes fallback answers while keeping citations intact.
  - Ingest + Ask with the stub provider yields citations, planner metadata, latency, and bounded confidence in the response body.
  - Chroma/Neo4j outages trigger warnings and in-memory fallbacks without user-visible errors.

## 12. Future Extension Points
- `backend/app/api/__init__.py` reserved for modular routers (currently unused).
- Add more embedding providers by extending `EmbeddingProvider` protocol and wiring `get_embedding_provider`.
- Frontend citations currently display raw chunk text; consider adding highlight or doc titles when available.
- For production, add persistence for chat history and authentication gates.

Keep this file in sync after any structural or behavioural change so that it remains a ground-truth reconstruction guide.
