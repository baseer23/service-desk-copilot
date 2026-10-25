# Test inventory and consolidation audit

This inventory records the reviewed coverage groups. Individual parametrized cases remain visible through pytest/Vitest collection. Execution times are marked unmeasured where the no-test instruction prevented a fresh duration run.

| Test file / test name | Feature | Time | External dependencies | Paid risk | Relevance / duplicate coverage | Profile | Decision and reason |
|---|---|---:|---|---|---|---|---|
| `test_semantic_memory.py` / semantic-memory acceptance tests | Explicit facts, restart, vector recall, correction, jobs, forgetting, isolation | Unmeasured | Temp SQLite/Chroma, graph sink | None offline | Primary acceptance coverage | Fast when mapped; regression | Keep and extend; covers data integrity and recovery |
| `test_general_conversations.py` / restart, history, owner scope | LangGraph conversation continuity and registry | Unmeasured | Local MCP/checkpoints | None offline | Primary API persistence coverage | Fast when mapped; regression | Keep and extend; ordinary chats are not Cases |
| `App.test.tsx` / `restores the active conversation after a refresh` | React local pointer and durable restoration | Unmeasured | Mocked fetch, jsdom | None | Unique UI state contract | Fast for App changes; regression | Keep |
| `e2e.spec.ts` / `ordinary conversation restores the same thread after refresh` | Browser refresh acceptance | Unmeasured | Loopback FastAPI/Vite/MCP | None offline | Browser-only persistence proof | Regression | Keep |
| `e2e.spec.ts` / seven generic fictional status scenarios | Repeated submit/status smoke path | Historical browser cost | Loopback stack | None offline | Duplicated by stronger Jake, MFA and printer workflows | — | Remove; stronger retained cases assert routing, tools, resume and persistence |
| `e2e.spec.ts` / Jake investigation | Interrupt/resume, case memory, events | Unmeasured | Loopback stack | None offline | Unique multi-interrupt workflow | Regression | Keep |
| `e2e.spec.ts` / MFA and printer table | Distinct tool selection | Unmeasured | Loopback stack | None offline | Representative routing differences | Regression | Keep parametrized |
| `test_graph_repo.py` | Graph idempotency, provenance, scoping | Unmeasured | Mock/in-memory driver | None | Security and projection contract | Fast for graph changes; regression | Keep |
| `test_mcp.py`, `test_multi_mcp.py` | Real stdio, schemas, discovery and lifetime | Unmeasured | Local subprocess | None | Distinct transport boundary | Fast for MCP changes; regression | Keep |
| `test_mcp_security.py` | Read/write permissions and authority | Unmeasured | Local MCP | None | Essential security | Fast for MCP/security; regression | Keep |
| `test_local_persistence.py`, `test_storage_safety.py` | SQLite/Chroma integrity and recovery | Unmeasured | Temporary local stores | None | Essential data safety | Mapped; regression | Keep |
| `test_provider*.py` | Groq/Azure request contracts and provider selection | Unmeasured | Mock transports | None offline | Covers request compatibility without inference | Mapped; regression | Keep |
| `test_ingest_url.py`, `test_scope_source_policy.py` | URL safety, redirects, dedup and retrieval modes | Unmeasured | Mock/local HTTP only | None offline | Required URL-ingestion security | Mapped; regression | Keep |
| GPT-5 Nano branch: `test_provider_azure_openai.py`, `test_provider_selection.py`, `ProviderSelector.test.tsx` | Deployment routing, unsupported-parameter omission, missing deployment and UI selection | Previously passed on feature branch; not rerun | Mock client only | None | Focused contracts exist in PR branch `feature/gpt5-nano-url-ingestion` | Mapped after integration; regression | Keep; selector already maps provider files |
| URL branch: `test_ingest_url.py`, `test_websites.py`, `KnowledgePanel.test.tsx` | Public URL ingestion, dedup, keyword contract and website UI | Previously passed on feature branch; not rerun | Mock crawler/local stores | None | Focused contracts exist in PR branch `feature/gpt5-nano-url-ingestion` | Mapped after integration; regression | Keep; website mapping included |
| `test_speech_stt.py`, `VoiceSupport.test.tsx` | Speech state and UI lifecycle | Unmeasured | SDK/browser doubles | None offline | Unique voice boundary | Voice changes; regression | Keep; never selected for unrelated edits |
| `test_optional_slow.py::test_ingestion_with_aura` | Live Aura integration | Environment-dependent | Live Neo4j | External-service risk | Not an offline regression | Live only | Retain and mark `live`; explicit authorization required |
| `test_offline_network.py` | Credit-safety socket guard and loopback | Unmeasured | Local socket | None | New safety regression | Fast for harness changes; regression | Add |
| `test_test_selector.py` | Changed-path mapping and fail-closed expansion | Unmeasured | Git-free unit logic | None | New harness contract | Fast for harness changes; regression | Add |

No security, persistence, recovery or data-integrity suite was removed. Further deletion requires fresh duration data and assertion-level comparison.
