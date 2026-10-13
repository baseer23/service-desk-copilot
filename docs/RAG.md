# Supervisor, reasoning and capability boundary

Azure OpenAI supplies structured reasoning. Browser Voice Support uses Azure Voice
Live for speech and a bounded application-owned LangGraph for investigation over
the existing local stdio MCP client. See [VOICE.md](VOICE.md) for session boundaries
and [AZURE_LIVE_SETUP.md](AZURE_LIVE_SETUP.md) for local acceptance setup.

React → FastAPI → LangGraph supervisor → bound LangChain StructuredTool.ainvoke → async MCP → capabilities.

The supervisor has classify_intent, understand_request, plan, execute_tools, observe, reason, verify, ask_human, research, remember and finalize nodes. Knowledge and service-desk workflows are separately compiled LangGraph subgraphs; each performs understanding, validated initial planning, MCP execution and evidence observation, then shares the supervisor reasoning/verification loop. The knowledge path formulates document retrieval without operational questions. The case path analyzes identifiers/scope, loads confirmed memory and plans contextual investigation. Subsequent rounds replan from new evidence or verifier gaps.

Azure OpenAI supplies typed RequestIntent, TicketAnalysis, InvestigationPlan, InvestigationDecision, ResearchSelection and VerificationResult. Python validates discovered tool schemas, read permissions, maximum tool count, parallel group indexes and cited evidence IDs. Independent groups use asyncio.gather; one task owns the SDK stdio contexts while concurrent SDK requests share that session. Write tools are unavailable to model plans. Human confirmations and explicitly observed trusted record fields alone produce semantic facts. No generated hypothesis is persisted as a fact.

EvidenceItem records source/system/type, tool, timestamp, entities, reliability, retrieval relevance, case ID and document/URL provenance. Confirmed facts, human confirmations, public documentation, inferences and hypotheses stay distinct. Conflicting memory values remain visible with their timestamps; the model evaluates freshness and provenance. A separate structured critic checks the current candidate and source evidence independently of earlier critic text or preliminary analysis hypotheses. It checks supported/unsupported claims, contradictions, pending question materiality and evidence gaps. Premature causation or compound/unjustified questions are revised before interrupting the human. Unsupported final claims are withheld; empty evidence and exhausted cycle budgets produce insufficient_information and explicit uncertainty. Retrieval relevance is separate from investigation confidence.

Human input uses interrupt() and Command(resume=...) on the same case thread. Request events are idempotent across replay; answers and actual actions are episodic memory accessed through MCP. Checkpoints alone use direct SQLite infrastructure. Case snapshots, semantic memory, event/history reads, ingestion, fixtures, web and retrieval all cross MCP, including FastAPI ingestion routes. A new supervisor checkpoint file preserves old graph checkpoints without pretending incompatible paused states can resume in a new topology.

MCP exposes typed input/output, description, tool, source_system, timestamp, reliability, status/error and items. The inventory includes knowledge/graph/ticket/context/alert/change/web tools, ingest_text/ingest_document, remember_fact/search_memory, case notes/history and list_available_context. Additional infrastructure tools own case snapshot persistence. Existing Retriever, Chroma, IngestService and SQLite adapters remain behind the boundary.

Aura is optional. search_graph uses bounded two-hop relationship discovery for WORKS_AT, HAS_SITE, USES, AFFECTS, APPLIES_TO, INVOLVES and ASSOCIATED_WITH; fixture edges explicitly carry local_fixture provenance. An unavailable Aura returns unavailable status and never masquerades as a live graph result. Existing document/entity/chunk relationships still enrich retrieval.

Chroma stores documents, KBs, web research and useful notes, never transactional case state. Azure embedding vectors and test vectors stay in separate persistent collections. SHA-256 exact deduplication and source metadata are retained; no search error resets stored data. MCP ingestion makes new documents immediately searchable.

Research starts with a specific model-generated evidence gap/query. A structured source selection prefers primary/vendor pages and selects only actual returned URLs. Actual fetched content receives a second usefulness assessment before ingestion; fetching, ingestion and subsequent retrieval happen through MCP. Failed research capabilities become explicit error evidence. Research has a separate round limit. Public content remains untrusted data. Shared crawler/extraction code enforces public URLs, redirect checks, body limits, robots/same-origin/page/depth/text/rate bounds.

Tests instrument actual LangChain calls and real MCP requests, prohibit repository access in the application process, prove parallel requests, run conditional cycles/critic/interrupt/restart/resume, and cover resume, skills, PDF, VPN, MFA, printer and shared outages. Browser tests use an isolated real HTTP/MCP stack with deterministic backend providers. Previous live smoke results predate the Azure migration and do not validate Azure.

The existing structured intent classifier also returns `in_scope`. With
`KNOWLEDGE_SCOPE=support` (default), clearly unrelated general questions return a
scope explanation before case creation, retrieval, web calls or ingestion. IT,
internal-policy and uploaded-document questions are accepted. `broad` is an explicit
operator opt-in. This model judgment can misclassify; it is not a keyword security
boundary.

`RESEARCH_SOURCE_POLICY=prefer_primary` ranks configured vendor/government domains
first. `primary_only` filters before fetch and after redirects; `allow_third_party`
keeps all permitted sources. `RESEARCH_PREFERRED_DOMAINS` accepts a comma-separated
list. Source labels remain visible. Fetching alone never authorizes ingestion: the
content evaluator must select the page and set `directly_relevant=true`. Search
snippets remain discovery only, never diagnostic evidence. Starting acceptance with
fresh Chroma avoids previous irrelevant knowledge without deleting old collections.
