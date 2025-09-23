# Service Desk Copilot — Agent Notes

## Sprint 2 Snapshot
- FastAPI backend now supports hybrid GraphRAG:
  - `/ingest/paste` and `/ingest/pdf` chunk incoming content, embed via Ollama/sentence-transformers/stub, persist to Chroma (vectors) and Neo4j (graph).
  - `/ask` performs planner → retrieval → answer synthesis, returning citations, planner metadata, latency, and confidence. Stub provider remains the default.
- Optional local LLMs: Ollama (`MODEL_PROVIDER=ollama`) and llama.cpp (`MODEL_PROVIDER=llamacpp`). When unavailable the stub path keeps everything local and deterministic.
- React frontend exposes an Ingest panel (Paste | PDF), real-time chat with citations, and a recent question sidebar. Conversation state is no longer persisted across refresh.
- Tooling additions: Docker Compose launches Neo4j (`neo4j:5.25.1`, APOC enabled) with volumes rooted under `~/Documents/service-desk-copilot/neo4j` (Docker Desktop friendly) and an optional commented Ollama service; Makefile targets cover `compose-up`, `compose-down`, `ingest-sample`, plus the existing dev/fmt/test helpers.

## Repository Layout (updated)
```
.
├── .editorconfig
├── .env.example
├── docker-compose.yml
├── agent.md
├── backend/
│   ├── logging.ini
│   └── app/
│       ├── core/config.py
│       ├── main.py
│       ├── models/
│       │   ├── dto.py
│       │   ├── provider.py / factory / stub / ollama / llamacpp
│       ├── adapters/embeddings.py
│       ├── services/
│       │   ├── chunking.py
│       │   ├── entities.py
│       │   └── ingest_service.py
│       ├── store/
│       │   ├── graph_repo.py
│       │   └── vector_chroma.py
│       ├── rag/
│       │   ├── planner.py
│       │   ├── retrieve.py
│       │   └── answer.py
│       └── tests/
│           ├── test_answer.py
│           ├── test_chunking.py
│           ├── test_dto.py
│           ├── test_embeddings.py
│           ├── test_entities.py
│           ├── test_graph_repo.py
│           ├── test_ingest_integration.py (slow, optional)
│           ├── test_ingest_service.py
│           ├── test_planner.py
│           ├── test_provider.py
│           ├── test_retrieve.py
│           └── test_vector_store.py
├── docs/
│   ├── README.md
│   └── RAG.md
├── frontend/
│   ├── README.md
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   └── src/
│       ├── App.tsx
│       ├── main.tsx
│       ├── styles.css
│       └── components/
│           ├── Composer.tsx
│           └── MessageBubble.tsx
├── scripts/
│   ├── dev.sh
│   ├── start_slm.sh
│   └── neo4j/00_constraints.cypher
├── store/
│   ├── .gitkeep
│   └── chroma/ (created at runtime)
└── Makefile
```

## Backend Notes
- **Config (`core/config.py`)**
  - Adds Neo4j (`neo4j_uri/user/password`), Chroma directory, embedding provider settings (`embed_provider`, `embed_model_name`, `ollama_embed_model`, `ollama_host`), and planner knobs (`top_k`, `chunk_tokens`, `chunk_overlap`).
  - `get_settings()` now creates both `logs/` and `store/chroma` directories.

- **Ingestion (`services/ingest_service.py`)**
  - `ingest_text` → `split_text` → embed → `VectorChromaStore.upsert` → `GraphRepository` upserts + entity linking → returns `IngestPasteResponse` (chunks/entities/vector_count/ms).
  - `ingest_pdf` extracts text via pdfminer, tracks page count, reuses the text pipeline.
  - `split_text` in `services/chunking.py` uses env-driven chunk/overlap sizes with deterministic token estimation.

- **Graph & Vector stores**
  - `GraphRepository` (Neo4j driver) + in-memory fallback for offline mode. Supports constraint bootstrap, document/chunk/entity upserts, relationship linking, and entity-degree queries.
  - `VectorChromaStore` manages a persistent Chroma collection (`chunks`). `InMemoryVectorStore` fallback retains data in-memory when Chroma is unavailable.

- **Planner / Retriever / Responder (`backend/app/rag`)**
  - `Planner` extracts entities from the question, checks graph degree, and chooses GRAPH / VECTOR / HYBRID, returning reasons + `top_k`.
  - `Retriever` performs vector search (Chroma), graph search (Neo4j), or hybrid filtering.
  - `Responder` builds a prompt, queries the configured provider (stub | Ollama | llama.cpp), and emits an `AskResponse` with citations (including snippets), planner metadata, latency, and confidence.

- **Routes in `main.py`**
  - Startup initialises vector store (Chroma with fallback) and graph repo (Neo4j with fallback) and caches them on `app.state`.
  - `/ingest/paste` and `/ingest/pdf` instantiate `IngestService` per request, enforce payload limits (5 MiB for ingestion), and return DTO responses.
  - `/ask` orchestrates plan → retrieve → answer. Body limit for questions remains 1 MiB.
  - Graceful fallbacks: stub embeddings when Ollama/sentence-transformers unavailable; in-memory graph/vector stores if services are offline.

## Frontend Notes
- No more localStorage persistence—refreshing the page clears the thread.
- Ingest panel (Paste | PDF) posts to `/ingest` endpoints and shows counts.
- Chat messages include citations (expandable) and recent questions appear in a sidebar.
- API base URL resolves from `import.meta.env.VITE_API_BASE` or falls back to `window.location.origin`.

## Tooling
- `docker-compose.yml` provisions Neo4j Community with APOC. Optional Ollama service commented out.
- Make targets:
  - `make dev` — uvicorn + Vite with cleanup trap.
  - `make compose-up` / `make compose-down` — manage docker services.
  - `make ingest-sample` — posts a sample document to `/ingest/paste`.
  - `make fmt` — Ruff/Black/Prettier.
  - `make test` — pytest suite (unit + optional slow integration).

## RAG Flow Overview
See `docs/RAG.md` for the ASCII diagram. High-level:
1. **Ingest** → chunking → embeddings → Chroma + Neo4j.
2. **Ask** → planner decides GRAPH/VECTOR/HYBRID → retriever gathers contexts → responder synthesizes answer with citations.
3. Stub path remains deterministic; launching Ollama/llama.cpp upgrades the answer while keeping citations intact.

Keep this file aligned with future changes so new prompts can rely on a single authoritative source.
