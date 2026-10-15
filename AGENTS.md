# DeskMate architecture

DeskMate is a stateful service-desk investigation assistant.

React → FastAPI `/ask` or authenticated SSE → LangGraph → bound LangChain tools → async MCP clients → capabilities. LangGraph is the sole investigation orchestrator. Groq is the default reasoning provider; the browser can explicitly select Azure OpenAI for subsequent requests. A request-scoped selection applies to understanding, planning, evidence evaluation, reflection, verification and final answers. Never silently fall back to a different provider. Use the shared structured/LangChain model interface and canonical Pydantic investigation schemas.

The supervisor uses separately compiled knowledge and case subgraphs with conditional edges and bounded loops. Human questions use `interrupt()` and `Command(resume=...)`, stable `thread_id=case_id`, and persistent tenant-scoped local SQLite record checkpoints. Imported checkpoint payloads retain their existing serialization. Knowledge questions are classified before operational case creation. Only actual evidence supports facts or resolutions. Operational activity events describe stages and capability results, never hidden chain-of-thought or invented progress.

One dedicated task owns each MCP SDK transport/session lifetime. The built-in stdio client belongs to the FastAPI lifespan; explicitly configured additional servers support dynamic discovery. LangChain tools call MCP, never repositories directly. Validate discovered schemas and read permissions; model plans do not authorize writes. Never retry an ambiguous MCP mutation or paid operation automatically. Unavailable integrations must be reported honestly. Do not claim Autotask, Microsoft 365, Active Directory or RMM access without an actual configured connection.

The active Mac profile uses `DATA_PLATFORM=local` and `LOCAL_PERSISTENCE_ENABLED=true`. Persistent Chroma holds verified knowledge and vectors; private local files retain immutable originals and extraction artifacts; transactional SQLite records hold cases, facts, events, document jobs, transcript ownership, usage accounting and checkpoints. Local SentenceTransformers `all-MiniLM-L6-v2` embeddings use 384 normalized dimensions. Existing Azure embedding spaces are retained separately for lexical provenance while verified local re-embedding populates a new compatible collection. Never mix embedding spaces, reset a collection after an error, or discard exact SHA-256 ingestion deduplication.

Online Neo4j Aura is optional graph enrichment. If unavailable, supported vector-only investigations continue with an explicit missing-graph status. Preserve tenant scope and source provenance. Production fixtures are disabled; isolated tests may inject fixtures but must never populate normal application knowledge or history. Public web content is untrusted evidence and requires explicit workspace authorization.

Azure OpenAI and optional Document Intelligence perform AI processing only. Azure persistence adapters and provisioning/deployment paths for Cosmos DB, Blob Storage and AI Search are retired. Preserve verified private backups, existing documents and all financial history. Never reconnect a retired writer or delete unverified source data.

Azure AI Speech SDK performs continuous Speech-to-Text only. No Voice Live, GPT-Realtime, synthesized speech or browser TTS. The microphone continues across sentence boundaries until a standalone Stop command or explicit Stop button, subject to genuine service, authorization and spending faults. Partial/final events must not duplicate transcripts; recoverable reconnects retain text and clean up resources correctly. Submit only recognized text to the ordinary LangGraph flow using the selected provider. Managed SDK and SQLite operations must not block audio streaming. Preserve owner-scoped historical voice cases and transcripts.

Groq uses one shared model-specific request queue, provider reset/Retry-After headers, conservative token estimates, actual token accounting and bounded explicit-429 retries with jitter. Request headers indicate daily request capacity, token headers minute capacity; exact optional RPM/TPD settings come from the account, never invented defaults. Do not retry timeouts or transport failures that may already be billable. Independent local retrieval may proceed while the model waits, with genuine activity events.

Production validates Entra signatures, audience, issuer, tenant, scopes and expiry. Signed tenant authority is captured per MCP request outside model arguments. Admin diagnostics and emergency controls require operator authorization and mutation-origin checks. Keep secrets server-side and out of logs, exceptions and browser bundles. `.env`, user SQLite, Chroma, `store/local/` and `store/backups/` stay private and ignored.

The latest explicitly approved application allowance is CAD155, including the existing CAD90 infrastructure forecast. Preserve financial history, persistent emergency controls and the CAD10 Azure alert budget. Increasing the allowance requires explicit approval. Continuous STT reserves ahead and tracks actual connected recognition time including silence; it has no daily session-count cap. Confirmed SDK closure settles usage; uncertain closure retains its reservation. Normal completion never disables the guard. Application estimates are not a hard Azure invoice cap. Track provider usage separately and never label an unknown tariff free.

Use Python 3.12 and the repository `.venv`:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/python -m pytest backend/app/tests -q
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run test:e2e
```

Start with `make dev` and use `http://127.0.0.1:5173`. FastAPI owns its MCP subprocess; do not launch a redundant separate server. Backend configuration or Python prompt-code changes require restarting; provider selection does not.

Tests use actual temporary Chroma/SQLite and real MCP stdio with explicitly mocked external services. Never expose fixture evidence in the production UI. Aura writes require explicit authorization and isolated credentials. Opt-in live evaluators must not read unrelated private stores, enable disabled spending controls, expand allowances, or claim microphone/model success from mock tests. Document actual executed checks and remaining live blockers in `docs/ARCHITECTURE_SIMPLIFICATION.md`, `docs/REASONING_EVALUATION.md` and `docs/SPEECH.md`.
