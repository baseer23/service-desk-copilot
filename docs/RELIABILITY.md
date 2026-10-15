# Reasoning and research reliability

DeskMate preserves the existing React → FastAPI → LangGraph → LangChain tool → MCP architecture. The reliability layer limits the transient model view; it never deletes persisted evidence, checkpoints or local knowledge.

## Model budgets

`openai/gpt-oss-120b` and `openai/gpt-oss-20b` use their documented 131,072-token Groq context windows. `GROQ_CONTEXT_WINDOW_TOKENS=0` selects the known model limit. Unknown models use a conservative 32,768-token default unless an operator supplies a verified limit.

Each request reserves stage-specific output, protocol and schema overhead. The packer retains the current question, contradictions, recent conversation, recent evidence excerpts and source provenance. It bounds tool schemas, source count and individual text fields. When Groq reports a context overflow despite local estimation, DeskMate records a compaction event and retries once with a smaller read-only model view. A malformed structured response receives one bounded correction attempt. Rate-limit retries remain limited to explicit HTTP 429 responses.

## Provider recovery

Provider selection remains request-scoped, so concurrent investigations cannot change each other's provider. `PROVIDER_FALLBACK_ENABLED=false` is the default. If an operator enables it, a recoverable Groq capacity or structured-output failure can switch once to an already configured Azure OpenAI deployment. The switch appears in the activity timeline. Azure remains subject to the existing usage guard and spending policy. Authentication, authorization and configuration failures do not trigger fallback loops.

Azure errors are separated into authentication, authorization, configuration, capacity, context-length, timeout and temporary-service categories. Browser messages contain an action and, when Azure returns one, a sanitized request identifier. Tokens, request bodies, endpoints and credentials are never emitted.

## Research behavior

Public research requires `ENABLE_PUBLIC_WEB_TOOLS=true`. LangGraph creates small evidence-gap queries, MCP validates tool permission and arguments, and search results are deduplicated and ranked by the configured source policy. Search snippets are discovery aids. DeskMate fetches selected pages, checks actual content, and keeps the resulting evidence inside the investigation with its original URL and title. Routine public research does not write fetched pages into persistent Chroma.

Malformed, empty or unreachable MCP results become explicit error evidence. One failed source does not erase evidence collected from other sources. Tool mutations and paid operations are never replayed during provider recovery.

## Explainability

The streamed timeline shows actual workflow events: question understanding, tool discovery, research plan, selected tool, public query, evidence count, source links, evidence evaluation, verification, fallback and final assessment. These are bounded decision summaries and operation outcomes. They do not expose hidden model reasoning. Stable event identifiers and sequence numbers prevent duplicate display after recovery.

## Evaluation

`scripts/evaluate_reasoning.py` contains seventeen diverse prompts and ten explicit metrics: understanding, clarification, tool selection, search quality, evidence quality, reasoning quality, citation accuracy, efficiency, resilience and explainability. It includes the exact `my vpn isnt working` regression, ambiguous and misspelled requests, factual and current questions, multi-source research, unavailable evidence and an incorrect premise.

The evaluator refuses to call a live provider unless `--live` is supplied. Reports use isolated SQLite and Chroma paths and private file permissions. Simulated test results and real-provider results must be reported separately.
