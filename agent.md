# Service Desk Copilot / DeskMate

This file is the LLM-facing source of truth for the current repository. It is based on the code and tests that exist now, not on older sprint notes. If this file conflicts with `docs/README.md`, `frontend/README.md`, or stale comments, trust the code and the tests.

## 1. Project in one paragraph

Service Desk Copilot is a local-first RAG playground for service desk knowledge. The backend is a FastAPI app that ingests pasted text, PDFs, and optionally crawled web pages; chunks the content; creates embeddings; stores vectors in Chroma; stores document/chunk/entity relationships in either Neo4j Aura or an in-memory graph; retrieves relevant chunks with vector, graph, or hybrid search; and asks a language-model provider to answer with citations. The frontend is a Vite + React SPA branded as `DeskMate`.

## 2. The most important implementation truths

- The UI name is `DeskMate`, but the repo and backend app name are `Service Desk Copilot`.
- All live API routes are defined in `backend/app/main.py`. `backend/app/api/` is currently just an empty placeholder package.
- `MODEL_PROVIDER=auto` is not truly auto in the running FastAPI app today. `backend/app/main.py` maps `auto` and `local` to the active provider key `ollama`, so the live server behaves like "manual Ollama using MODEL_NAME" plus a stub fallback when requests fail.
- The provider factory itself does support a real `auto` path in `backend/app/models/provider_factory.py`, but the main app does not use that path for its active provider state.
- The graph backend only initializes a real `GraphRepository` when `NEO4J_URI` starts with `neo4j+s://` or `neo4j+ssc://`. A local Docker Neo4j on `bolt://localhost:7687` does not become the active graph backend in the current app.
- Because of that, the default local setup is usually `persistent Chroma + in-memory graph`, not `persistent Chroma + persistent Neo4j`.
- The in-memory graph is functional during the current process. It is not the same as "graph disabled". Ingested entities/chunks can still drive `GRAPH` or `HYBRID` planning while the server stays up.
- The in-memory graph does not survive restart. Chroma does survive restart. After restart, old vectors can still be searched, but graph relationships are gone until the content is ingested again.
- The frontend currently labels non-Aura graph mode as effectively "vector-only", which is an oversimplification. It is only truly vector-only after a restart or when no in-memory graph data exists.
- URL ingestion exists and works, but it is off by default. It requires `ALLOW_URL_INGEST=1`.
- Frontend chat history and ingest state are not persisted across refresh. The only client-side persistence is the provider preference stored in `localStorage` under `deskMate.providerPreference`.
- The frontend sends `provider_override` on every `/ask` request, so the UI chooses the provider per request. The admin provider toggle endpoint mostly keeps server state and `/health` aligned.
- Provider failures fall back to the deterministic stub answer, but the response `provider` field stays as the original provider name. Example: a Groq failure still returns `provider: hosted-groq` plus a stub-prefixed answer.
- Chroma fallback quality is intentionally weak. `InMemoryVectorStore.search()` ignores similarity and returns the first `top_k` records.
- Vector metadata currently stores `doc_id` and `ord`, but not the document title or source URL. Vector-only citations therefore often have no title.
- The workspace `.env` file contains real service credentials. Treat it as sensitive and never paste secrets into prompts, commits, or docs.

## 3. High-level architecture

User flow:

1. A user ingests content through `POST /ingest/paste`, `POST /ingest/pdf`, or `POST /ingest/url`.
2. The backend splits the content into chunks, computes embeddings, writes vectors to Chroma, writes document/chunk/entity data to the graph repo, and returns counts.
3. A user asks a question through `POST /ask`.
4. The planner chooses `VECTOR`, `GRAPH`, or `HYBRID`.
5. The retriever pulls chunks from the vector store, graph repo, or both.
6. The responder builds a prompt from those chunks, calls the chosen provider, and returns an answer plus a separate citation array.
7. The frontend renders the answer, sanitizes raw citation IDs from the visible text, and shows citation chips built from the backend citation payload.

## 4. Repository map

Top-level directories and files that matter:

- `backend/`: FastAPI app, RAG logic, stores, adapters, and tests.
- `frontend/`: Vite + React TypeScript SPA.
- `docs/`: supporting docs. Useful, but partially stale.
- `scripts/`: dev helpers for starting the app, local model helpers, and a small benchmark.
- `templates/`: example `.env` fragments for different operating modes.
- `store/`: runtime storage. Chroma lives under `store/chroma`.
- `logs/`: runtime logs such as `logs/app.log` and `logs/ollama.log`.
- `data/`: placeholder directories (`raw/`, `processed/`) that are currently unused by runtime code.
- `docker-compose.yml`: Neo4j container definition.
- `Makefile`: common dev/test commands.
- `agent.md`: this file.

## 5. Backend: actual runtime behavior

### 5.1 Entry point and app state

`backend/app/main.py` is the real application entry point.

- Creates the FastAPI app.
- Configures CORS from `Settings.allowed_origins`.
- Initializes app state for vector store, graph repo, graph driver, provider, provider context, and active provider key.
- On startup it re-loads settings, re-initializes the vector store and graph repo, and re-applies the active provider.
- On shutdown it closes the Neo4j driver if one exists.
- If `frontend/dist` exists, FastAPI serves the built SPA at `/` via `SpaStaticFiles`.

### 5.2 Request limits and middleware

- `POST /ask` is limited to 1 MiB.
- `POST /ingest/paste` and `POST /ingest/pdf` are limited to 5 MiB.
- The body-limit middleware checks `Content-Length` when present and otherwise buffers the body.
- A logging middleware logs `<METHOD> <PATH> -> <status>`.

### 5.3 API routes

`GET /health`

- Returns provider metadata, active provider key, reachability probes, graph backend, and vector store path information.
- Fields include `provider`, `provider_type`, `model_name`, `provider_vendor`, `local_model_available`, `operator_message`, `hosted_reachable`, `hosted_model_name`, `active_provider`, `active_model`, `graph_backend`, `preferred_local_models`, `ollama_reachable`, `llamacpp_reachable`, `neo4j_reachable`, `vector_store_path`, and `vector_store_path_exists`.

`POST /ingest/paste`

- Accepts `IngestPasteRequest { title?, text }`.
- Rejects payloads over 5 MiB.
- Builds an `IngestService` and indexes the pasted text.

`POST /ingest/pdf`

- Accepts multipart upload plus optional `title`.
- Only accepts `application/pdf` and `application/octet-stream`.
- Uses `pdfminer.six` to extract text.
- Returns page/chunk/entity/vector counts.

`POST /ingest/url`

- Accepts `IngestUrlRequest { url, max_depth?, max_pages? }`.
- Requires `ALLOW_URL_INGEST=1`.
- Crawls same-origin HTML pages only, respects `robots.txt`, deduplicates by canonical URL and content hash, extracts article/main text, and ingests each crawled page with `IngestService`.

`POST /ask`

- Accepts `AskRequest { question, top_k?, provider_override? }`.
- Runs planner -> retriever -> responder.
- `provider_override` can be `ollama`, `groq`, `stub`, or `llamacpp`.
- Returns `AskResponse { answer, provider, question, citations, planner, latency_ms, confidence }`.

`POST /admin/provider`

- Accepts `ProviderToggleRequest { provider }`.
- Only supports `ollama` and `groq`.
- If `ADMIN_API_SECRET` is set, the request must include header `x-admin-secret`.
- Updates the active provider stored in app state.

### 5.4 Settings and environment

`backend/app/core/config.py` defines `Settings` using `pydantic-settings`.

Important variables:

- `APP_NAME`
- `ALLOWED_ORIGINS`
- `MODEL_PROVIDER`
- `MODEL_NAME`
- `MODEL_TIMEOUT_SEC`
- `ADMIN_API_SECRET`
- `ALLOW_URL_INGEST`
- `URL_MAX_DEPTH`
- `URL_MAX_PAGES`
- `URL_MAX_TOTAL_CHARS`
- `URL_RATE_LIMIT_SEC`
- `NEO4J_URI`
- `NEO4J_USER`
- `NEO4J_PASSWORD`
- `CHROMA_DIR`
- `EMBED_PROVIDER`
- `EMBED_MODEL_NAME`
- `OLLAMA_EMBED_MODEL`
- `OLLAMA_HOST`
- `LLAMACPP_HOST`
- `HOSTED_MODEL_NAME`
- `GROQ_API_KEY`
- `GROQ_API_URL`
- `TOP_K`
- `CHUNK_TOKENS`
- `CHUNK_OVERLAP`
- `VITE_API_BASE` and `VITE_ADMIN_API_SECRET` are frontend-side variables used in the browser build, not backend settings.

Other config facts:

- `get_settings()` is cached with `lru_cache`.
- `get_settings()` creates `logs/` and `store/chroma/` if they do not exist.
- `DEFAULT_STUB_ANSWER` is `hi, this was a test you pass`.

### 5.5 Provider model layer

Files:

- `backend/app/models/provider_factory.py`
- `backend/app/models/provider_ollama.py`
- `backend/app/models/provider_llamacpp.py`
- `backend/app/models/provider_groq.py`
- `backend/app/models/provider_stub.py`

Supported providers:

- `stub`: deterministic answer for tests and fallbacks.
- `ollama`: posts to `{OLLAMA_HOST}/api/generate`.
- `llamacpp`: posts to `{LLAMACPP_HOST}/completion`.
- `groq` / `hosted`: posts to the Groq OpenAI-compatible chat completions endpoint.
- `auto`: only implemented in the factory helper, not as the live app's active provider behavior.

Important provider details:

- The factory's intended preferred local model order is `phi3:mini`, then `tinyllama`.
- The running app's default path effectively pins Ollama to `MODEL_NAME`.
- `GroqHostedProvider` requires `GROQ_API_KEY` at construction time.
- Provider errors are caught in `Responder.answer()` and converted to a stub-prefixed answer.

### 5.6 Embeddings

`backend/app/adapters/embeddings.py` defines the embedding layer.

Supported embedding providers:

- `sentence`: `SentenceTransformersEmbeddingProvider`
- `ollama`: `OllamaEmbeddingProvider`
- `stub`: deterministic hash-based embeddings
- `auto`: Ollama if reachable, else sentence-transformers, else stub

Important embedding facts:

- The default config is `EMBED_PROVIDER=sentence`.
- The stub embedding provider returns deterministic unit-length pseudo-random vectors.
- If the chosen embedding provider fails during ingest, `IngestService` falls back to `StubEmbeddingProvider`.

### 5.7 Ingestion pipeline

Core files:

- `backend/app/services/chunking.py`
- `backend/app/services/entities.py`
- `backend/app/services/ingest_service.py`
- `backend/app/services/url_crawler.py`

Chunking behavior:

- Chunking is whitespace-based, not tokenizer-based.
- `split_text()` uses `chunk_tokens` and `chunk_overlap`, either from args or from env vars.
- Overlap is clamped so it cannot be negative or greater than/equal to chunk size.
- `approx_tokens()` is heuristic and mixes word count with a char-based estimate.

Entity extraction:

- Tries `spacy` with `en_core_web_sm` if available.
- Falls back to regex extraction otherwise.
- Entities are normalized to lower-case strings.

Text ingest:

- `IngestService.ingest_text()` creates a new `doc_id`, creates per-chunk IDs of the form `{doc_id}-{ord}`, embeds chunk text, upserts vectors, upserts document and chunk nodes, extracts entities, and links chunk-to-entity edges when the entity string appears in the chunk text.
- Graph document nodes store `title` and a `source`, but `ingest_text()` does not pass a custom `source`, so the graph-level source defaults to `paste` even for PDF and URL ingestion.
- Vector metadata currently stores only `doc_id` and `ord`.

PDF ingest:

- `IngestService.ingest_pdf()` extracts text using `pdfminer.high_level.extract_text`.
- Page count is estimated by counting form-feed characters (`\f`), so it is approximate.

URL ingest:

- `UrlCrawler` only allows `http` and `https`.
- It stays on the same origin as the root URL.
- It respects `robots.txt`.
- It skips non-HTML responses.
- It removes `script`, `style`, `noscript`, `template`, `nav`, `footer`, `form`, `aside`, and `iframe`.
- It extracts text mainly from `article`, `main`, or `body` via headings and paragraphs.
- It rate-limits requests per host.
- It deduplicates by canonical URL and page content hash.
- It does not persist the original URL into vector metadata or graph metadata today.

### 5.8 Stores

Vector store: `backend/app/store/vector_chroma.py`

- `VectorChromaStore` persists to disk under `CHROMA_DIR`.
- It uses a Chroma collection named `chunks`.
- If Chroma metadata types are incompatible with old data, the store resets the collection.
- Search returns items shaped like `{ id, text, metadata, score }`.

In-memory vector fallback:

- `InMemoryVectorStore` is deterministic but low-fidelity.
- Its `search()` method ignores similarity and returns the first `top_k` records in insertion order.

Graph store: `backend/app/store/graph_repo.py`

- `GraphRepository` wraps Neo4j reads/writes and can create constraints/indexes.
- `InMemoryGraphRepository` mirrors the same interface with plain Python dictionaries and sets.
- The in-memory graph stores enough data for planner and graph retrieval to work within the current process.

### 5.9 Planner, retriever, responder

Planner: `backend/app/rag/planner.py`

- Extracts entities from the question.
- Calls `graph_repo.get_entity_degrees()`.
- If there are no relevant entity degrees, returns `mode: VECTOR`.
- If the maximum degree is at least `GRAPH_THRESHOLD = 3`, returns `mode: GRAPH`.
- Otherwise returns `mode: HYBRID`.

Retriever: `backend/app/rag/retrieve.py`

- `vector_search()` embeds the question and queries the vector store.
- `graph_search()` finds entities in the question, filters to entities with degree > 0, and fetches chunks for them from the graph repo.
- `hybrid_search()` gets graph results first, then filters vector results to IDs allowed by the graph result set, falling back to plain vector results when needed.

Responder: `backend/app/rag/answer.py`

- Builds a prompt from the retrieved chunks.
- Uses a provider to generate the answer unless the provider is already `stub`.
- Builds the citation list directly from retrieved chunks. The citation list does not depend on whether the model produced inline citation text correctly.
- Confidence is derived from the mean retrieval score and clamped to `[0.1, 0.99]`, with `0.5` when there are no scores.

## 6. Frontend: actual behavior

The frontend is in `frontend/src`. `App.tsx` is the central state machine.

### 6.1 Main app behavior

`frontend/src/App.tsx` handles:

- health loading from `/health`
- provider selection and provider restore from `localStorage`
- asking questions through `/ask`
- ingesting paste, PDF, and URL content
- rendering the system-status popover
- rendering recent questions
- rendering the "Last answer" sidebar card
- resetting the current thread
- purging all client-side memory after a typed `DELETE` confirmation

Important frontend facts:

- `API_BASE` defaults to `http://localhost:8000` in dev and `window.location.origin` in production.
- `ADMIN_SECRET` comes from `VITE_ADMIN_API_SECRET` and is sent only to `/admin/provider`.
- Every ask request includes `provider_override: providerChoice`.
- The provider choice is restored from `localStorage` and synchronized with health data.
- The app does not persist messages, indexed sources, or recent questions across refresh.

### 6.2 Ingest UI

The ingestion panel has three modes:

- `Paste`: posts JSON to `/ingest/paste`.
- `Upload PDF`: posts multipart form data to `/ingest/pdf`.
- `URL`: posts JSON to `/ingest/url`.

Frontend ingest behavior:

- Success banners show chunks, entities, vectors, and page counts when available.
- The last indexed source can be viewed again.
- Pasted text opens as a text blob.
- PDF opens as a blob URL.
- URL opens the original URL in a new tab.

### 6.3 Chat UI

Files:

- `frontend/src/components/Composer.tsx`
- `frontend/src/components/MessageBubble.tsx`

Composer:

- Controlled textarea.
- Enter sends.
- Shift+Enter inserts a newline.

Message bubble behavior:

- Assistant answers are sanitized before display.
- The UI strips boilerplate phrases like "This information can be found at ...".
- The UI strips inline citation IDs such as `doc_id`, `chunk_id`, and bracketed tags from the visible answer text.
- Citations are shown as chips and an expandable drawer.
- Citation chips copy an excerpt, not the raw backend IDs.
- A debug panel exists in the component state, but there is currently no visible button that exposes it.

### 6.4 Styling and build

- `frontend/src/styles.css` contains the full UI styling.
- `frontend/src/main.tsx` mounts the app with `React.StrictMode`.
- `frontend/vite.config.ts` sets port `5173` for dev and preview and configures Vitest with jsdom.
- When `frontend/dist` exists, the backend serves it directly.

## 7. Commands and scripts

Main commands from `Makefile`:

- `make dev`: start FastAPI on `:8000` and Vite on `:5173`
- `make slm`: run `scripts/start_slm.sh`
- `make fmt`: Ruff fix + Black + frontend Prettier
- `make lint`: Ruff + frontend ESLint
- `make type`: mypy + frontend TypeScript typecheck
- `make test`: backend pytest + frontend Vitest
- `make security`: Safety + `npm audit`
- `make compose-up`: start Neo4j container
- `make compose-down`: stop Docker services
- `make ingest-sample`: send a sample paste-ingest request
- `make bench-air`: run the small local-vs-hosted benchmark

Script notes:

- `scripts/dev.sh` starts both servers and cleans them up on exit.
- `scripts/start_slm.sh` starts `ollama serve` if needed, checks for `phi3:mini` then `tinyllama`, and can also start a llama.cpp server if `LLAMACPP_BIN` and `MODEL_PATH` are provided.
- `scripts/mac_air_check.py` compares local Ollama latency with Groq latency and writes a short report to `logs/mac-air-check.txt`.

## 8. Tests that best describe intended behavior

Backend tests live in `backend/app/tests`.

The most informative tests for understanding the system are:

- `test_provider_paths.py`: provider selection, request-level overrides, admin toggle, provider failure fallback
- `test_provider.py`: baseline `/health` and `/ask` contracts plus ask payload-size limit
- `test_health_reachability.py`: meaning of `/health` fields
- `test_ingest_url.py`: URL ingest gate, robots handling, content extraction, same-origin crawl behavior
- `test_graph_repo.py`: Aura graph-repo path and Neo4j abstraction behavior
- `test_ask_end_to_end_stub.py`: ingest plus ask cycle through the stub provider
- `test_retrieval_integration_minimal.py`: vector plus hybrid retrieval with Chroma and in-memory graph
- `test_ingest_pdf_guard.py`: PDF round-trip when `pdfminer.six` is available
- `frontend/src/App.purge.test.tsx`: purge confirmation behavior and client-side state clearing

There is also a `slow` pytest marker in `pytest.ini`. `backend/app/tests/test_optional_slow.py` contains opt-in live-service checks for Neo4j and Ollama.

## 9. Files to read first when changing the code

If you need to understand or modify the app quickly, read these files in this order:

1. `backend/app/main.py`
2. `backend/app/core/config.py`
3. `backend/app/models/provider_factory.py`
4. `backend/app/services/ingest_service.py`
5. `backend/app/services/url_crawler.py`
6. `backend/app/rag/planner.py`
7. `backend/app/rag/retrieve.py`
8. `backend/app/rag/answer.py`
9. `backend/app/store/vector_chroma.py`
10. `backend/app/store/graph_repo.py`
11. `frontend/src/App.tsx`
12. `frontend/src/components/MessageBubble.tsx`
13. `backend/app/tests/test_provider_paths.py`
14. `backend/app/tests/test_ingest_url.py`
15. `frontend/src/App.purge.test.tsx`

## 10. Known gaps, drift, and non-obvious limitations

- `docs/README.md` and `frontend/README.md` are useful orientation docs, but they are not fully up to date with the real code.
- `docker-compose.yml` and `.env.example` suggest local Neo4j is a normal path, but the running backend only treats Aura-style URIs as a real graph backend.
- `make compose-up` may start Neo4j successfully without changing the app's actual `graph_backend`.
- The default runtime setup is therefore usually "persistent vectors, ephemeral graph".
- Since vector metadata lacks titles, citations can degrade to `Untitled` in vector-only paths.
- URL ingest does not store page URLs in retrieval metadata, so later citations do not point back to the original URL.
- The frontend graph warning text is stricter than the backend reality; the in-memory graph still supports graph lookups until restart.
- `featureFlags.ts` exists, but it currently only affects some UI label layout. It does not introduce major alternate flows.
- `backend/app/api/` exists but is not the routing layer.
- `data/raw/` and `data/processed/` are placeholders and are not used by the backend ingest pipeline today.

## 11. Short version

The real project is a FastAPI + React service-desk RAG app with Chroma persistence, optional Aura-backed graph persistence, a functional in-memory graph fallback, and provider-based answer generation through Ollama, llama.cpp, Groq, or a deterministic stub. The two biggest traps are that the running app does not really honor `MODEL_PROVIDER=auto` the way the factory suggests, and the default local Neo4j setup in docs does not become the active graph backend in the current `main.py`.
