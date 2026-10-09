# DeskMate

DeskMate investigates service-desk incidents, gathers evidence and asks for missing facts. A case is durable across requests and process restarts. Recommendations do not claim that a connection or incident has been resolved.

## Setup

Use Python 3.12 and Node.js 22. The only documented Python environment is the repository `.venv`.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
npm --prefix frontend ci
cp .env.example .env
```

For an existing installation, preserve `.env` and its credentials instead of copying over it. Configure `GROQ_API_KEY` and `HOSTED_MODEL_NAME=openai/gpt-oss-20b`. The default follows [Groq’s documented replacement](https://console.groq.com/docs/deprecations) for the model retired in August 2026. `GENERATION_PROVIDER=groq` is the live default. SentenceTransformers loads `all-MiniLM-L6-v2` on the first embedding request and may need to download its model. Configure `NEO4J_URI`, `NEO4J_USER`, and `NEO4J_PASSWORD` only for optional Aura; an empty URI uses vector retrieval.

```bash
make dev
```

Open **http://127.0.0.1:5173**. The backend runs on port 8000; Vite forwards case, ingestion and health requests. Stop both servers with Ctrl+C. There is no provider switch in the application.

## Investigation

Enter `Jake Peralta from B99 cannot connect to the VPN. Authentication failed.` and start an investigation. The agent extracts ticket details, loads remembered facts and checks independent context, incident, alert and change capabilities. With a fresh case database, it asks Jake's current floor, then whether the required client update was installed. Answer `2nd floor`, then `Yes`. Each answer resumes the existing LangGraph thread and is stored with human provenance. A second Jake case loads those confirmed facts before asking again.

The B99 enterprise records are **local fixtures**, including the WatchGuard alert and technician acknowledgement. They are never presented as live integrations. The timeline shows actual completed operations. The assessment separates hypotheses, confirmed facts, evidence support and uncertainty.

## API

| Method | Route | Purpose |
|---|---|---|
| GET | `/health` | Runtime and storage status |
| POST | `/ingest/paste` | Ingest `{text, title}` |
| POST | `/ingest/pdf` | Ingest a PDF multipart upload, maximum 5 MB |
| POST | `/ingest/url` | Crawl bounded public HTML when enabled |
| GET / POST | `/cases` | List cases / create `{summary}` |
| GET | `/cases/{id}` | Read the current assessment |
| POST | `/cases/{id}/investigate` | Start, retry a failed checkpoint, or return the existing paused/completed thread |
| POST | `/cases/{id}/resume` | Resume an interrupt with `{answer}` |
| GET | `/cases/{id}/events` | Read actual case operations |

Missing cases return 404. Model and capability failures return explicit errors. Use **Retry Investigation** after a transient service failure to continue the saved node in the same case. Hosted API rate limits still apply; pace live requests when the account has a small quota. Cases are `new`, `investigating`, `needs_input`, `assessment_ready`, `insufficient_information` or `error`.

## Persistence and verification

`CASE_DB_PATH` contains cases, confirmed facts and events. LangGraph uses the adjacent `<CASE_DB_PATH>.checkpoints.sqlite3` file to avoid write contention with MCP operational memory. Back up both files together while the application is stopped. Chroma stays at version 0.5.3, preserving the existing storage format.

Verified live embeddings use `chunks_sentence_minilm_v1`; isolated test vectors use `chunks_stub_v1`. Legacy `chunks` lacks reliable embedding provenance and remains untouched. Re-ingest original source documents into the verified collection to make that knowledge available; no automatic destructive migration occurs. SHA-256 hashes and deterministic chunk IDs prevent exact duplicate storage across process restarts.

```bash
.venv/bin/python -m pytest backend/app/tests -q
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run test:e2e
.venv/bin/python -m pip check
```

Playwright starts isolated servers on ports 8001/5174 with temporary SQLite/Chroma directories. Its backend uses deterministic test providers; browser requests are real HTTP and are not mocked. Vitest only discovers `src/**/*.test.{ts,tsx}`. Optional Aura tests are skipped unless explicitly enabled with isolated credentials.
