# Reasoning, retrieval and capability boundaries

The active flow is React → FastAPI → LangGraph supervisor → LangChain tools → MCP → local or connected capabilities. The selected Groq, Azure OpenAI, or independently configured Azure GPT-5 Nano provider is request-scoped, applies to every reasoning stage and does not change embeddings or Speech recognition. Instructions live in `backend/app/models/instructions.py`.

Knowledge URL ingestion is controlled by `ALLOW_URL_INGEST`, independently of the live-research `ENABLE_PUBLIC_WEB_TOOLS` flag. It validates public destinations and redirects, applies bounded depth/page/byte limits, extracts readable HTML, and indexes it into the existing local embedding collection with title, canonical URL, content hash and chunk provenance. The Knowledge excerpt search retains deterministic keyword retrieval; the LangGraph knowledge workflow uses hybrid semantic retrieval for grounded questions. The API also accepts explicit `HYBRID` and `VECTOR` modes. Neo4j remains optional.

## Investigation graph

`backend/app/agent/graph.py` compiles a supervisor and knowledge/service-desk subgraphs. Understanding and ticket analysis extract symptoms, identifiers, scope and missing information. Discovery supplies MCP descriptions and schemas. Planning validates a small read-only tool plan and independent parallel groups. Local evidence prefetch can run independently while Groq waits for capacity. Tool results become sourced evidence, then reasoning and a separate verifier assess claims, contradictions and gaps. Conditional edges permit bounded reflection/replanning or request one material human answer with a real LangGraph interrupt.

Knowledge requests remain separate from operational case creation. A retrieved document is not a live system observation. Untrusted source instructions cannot grant tool permissions. The graph distinguishes human reports, diagnostic observations, inferences and hypotheses. A candidate resolution stays unconfirmed until supported verification exists.

## Persistent local knowledge

`backend/app/rag/retrieve.py` owns retrieval behind MCP `search_knowledge`. Original PDFs, text and extraction artifacts stay in private local files. SHA-256 exact content hashes provide persistent deduplication. Chroma stores text, source/page provenance and local SentenceTransformers vectors. Transactional state, jobs, leases and checkpoints remain in SQLite rather than vector metadata.

Embedding provider, model, dimensions and normalization determine the collection identity. The active local model is `all-MiniLM-L6-v2`, 384 dimensions. Historical vectors from another model remain separate; lexical archives preserve their text/provenance without pretending their geometry matches the active model. Re-embedding uses local inference and verifies resulting counts before replacing an active collection reference. No retrieval failure resets a collection.

Online Neo4j Aura enriches retrieval with actual document/entity/dependency relationships. It is optional: missing/unavailable graph connectivity returns an honest unavailable result and supported Chroma searches continue. Production has no in-memory invented enterprise graph. Tenant-scoped relationships retain source provenance; fixtures remain confined to isolated tests.

## MCP and persistence

The lifespan owns the built-in MCP stdio transport. Additional explicitly configured MCP servers are discovered dynamically and namespaced. Input schemas, read permissions, maximum tool counts and parallel-group indexes are enforced before dispatch. Planned actions cannot invoke destructive write tools. Case snapshots, semantic memory, case history, document ingestion and retrieval all cross MCP; the checkpointer is the explicit direct-database exception.

Checkpoint state uses a stable conversation thread and tenant namespace. Human input resumes the persisted interrupt. Saved confirmed facts retain their provenance; model guesses do not become facts. Restart verification must read the same original file hashes, vector counts, case IDs and checkpoint payloads.

Document uploads use the existing local-first managed pipeline. Native text and text-bearing PDF pages are extracted locally. When configured and required for scans, screenshots, images, TIFF or Word layout, Azure Document Intelligence uses guarded `prebuilt-layout` requests with the existing free-tier segmentation and page reservation. Originals and extraction artifacts stay local. Chroma chunks include authenticated document provenance, page number, extraction method, document classification and per-page table count; table cells are ordered by row and column before indexing. Investigations retrieve these chunks only through MCP `search_knowledge`, and the UI presents page/extraction provenance without exposing private storage locations or credentials.

## Progress and limits

Authenticated SSE carries actual node/tool/capacity events to the expandable Investigation Activity panel. It preserves completed steps, distinguishes waiting from running and supports cancellation. It never transmits hidden internal model reasoning. Groq's documented headers supply daily-request and minute-token capacity; account-specific optional RPM/TPD limits must be configured from the current account rather than copied from illustrative documentation.

Use [reasoning evaluation](REASONING_EVALUATION.md) for executed orchestration checks and [current architecture](ARCHITECTURE_SIMPLIFICATION.md) for live verification boundaries. Mock providers can verify graph behavior, but cannot establish live model, Aura or physical microphone success.
