# Retrieval and investigation

The supported path is React → FastAPI case API → LangGraph investigation → bound LangChain tools → async MCP → capabilities. MCP is the protocol boundary, not a database or model. The official Python SDK runs a stdio subprocess whose full lifetime belongs to one async task. FastAPI starts it once and closes it in its lifespan. Transport loss permits one clean reconnect; MCP tool errors are propagated explicitly.

`InvestigationState` is the canonical durable graph state. `analyze_ticket` calls Groq for Pydantic-validated `TicketAnalysis`. Memory is loaded by subject across cases. The graph checks relevant tools, then Groq produces `InvestigationDecision` from the analysis, evidence, alerts, changes, knowledge, previous tickets, human answers and remembered facts. Decisions route to tools, a persisted human interrupt, public research or finalization. An eight-cycle default bound produces explicit uncertainty instead of unbounded recursion.

LangChain supplies schemas and bound invocation wrappers. The graph executes their `ainvoke` path; every capability wrapper calls MCP. No separate agent executor is used.

## Capabilities

- `search_knowledge` reuses the existing Retriever for vector, graph or hybrid retrieval.
- `search_previous_tickets`, `get_user_context`, `get_company_context`, `get_site_context`, `get_device_context`, `get_active_alerts`, `get_recent_changes` expose clearly labeled local fixtures.
- `save_case_note`, `remember_fact`, `search_remembered_facts` use the same SQLite path as FastAPI.
- `search_web` uses DDGS; `fetch_web_page` uses the same readable extraction and URL safety helpers as the crawler.

Confirmed facts include subject, predicate, value, source, source system, timestamp, confidence, reliability and originating case. Only human-confirmed, trusted-system or explicit resolved-outcome sources can be persisted. Model hypotheses are never facts. Prior facts retain timestamps so the model can consider staleness.

## Knowledge

Chroma stores 384-dimensional embeddings with SHA-256 content hashes, title, source, source type, URL, creation time, embedding provider and model metadata. Test/live embeddings are isolated in separate collections. Read and write failures raise storage errors without resetting data. The canonical IngestService handles text, PDF and extracted web content; live embedding failure is explicit.

Aura is optional. An unavailable Aura connection falls back to vector retrieval. Graph queries and Chroma retrieval remain low-level primitives, not orchestration. Vector distance measures retrieval relevance; case confidence comes from structured evidence assessment.

An empty local result can trigger public web research. Useful pages are ingested and knowledge is retrieved again. URL validation rejects non-HTTP schemes, credentials, local/private/link-local/reserved destinations and metadata endpoints. DNS is checked before requests, redirects are followed manually only after validation, and response bodies are bounded. The crawler respects same-origin scope, robots directives, depth/page/text limits and rate controls.

A final assessment remains a recommendation with explicit uncertainty. Without supporting evidence the status is `insufficient_information`; the agent never labels an empty result as resolved.
