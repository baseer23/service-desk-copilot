# DeskMate

Browser Voice Support uses Azure Voice Live, with a visible transcript and the existing
MCP investigation capabilities. See [AZURE_LIVE_SETUP.md](AZURE_LIVE_SETUP.md) for the
portal checklist and [READINESS_REVIEW.md](READINESS_REVIEW.md) for runtime traces.
Voice is disabled by default.

DeskMate routes document questions and service-desk incidents through a LangGraph supervisor, gathers evidence with model-selected MCP tools, and verifies candidate answers. A case is durable across requests and process restarts. Recommendations do not claim that a connection or incident has been resolved.

## Setup

Use Python 3.12 and Node.js 22. The only documented Python environment is the repository `.venv`.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
npm --prefix frontend ci
cp .env.example .env
```

For an existing installation, preserve `.env`. Configure `AZURE_FOUNDRY_ENDPOINT`
(the resource's Azure OpenAI endpoint), `AZURE_MODEL_DEPLOYMENT` and
`AZURE_EMBEDDING_DEPLOYMENT`. All live reasoning and embeddings use Azure;
`AZURE_USE_ENTRA_ID=true` uses DefaultAzureCredential. Local development can explicitly
select API keys with `AZURE_USE_ENTRA_ID=false`. See `.env.example` and the migration
notes in [VOICE.md](VOICE.md). Optional Aura uses `NEO4J_URI`, `NEO4J_USER` and
`NEO4J_PASSWORD`; an empty URI uses vector retrieval.

```bash
make dev
```

Open **http://127.0.0.1:5173**. The backend runs on port 8000; Vite forwards case, ingestion and health requests. Stop both servers with Ctrl+C. There is no provider switch in the application.

## Requests

Upload a resume using PDF/Paste, then enter `What year did Abdul graduate?` and click **Run DeskMate**. The supervisor uses the knowledge subgraph and returns a document-grounded answer with sources. It does not ask ticket location questions. MFA and printer incidents use relevant identity/device evidence; multi-user outages prioritize shared service health.

## Investigation

Enter `Jake Peralta from B99 cannot connect to the VPN. Authentication failed.` and click **Run DeskMate**. The model extracts identifiers, loads remembered facts, and proposes justified context, incident, alert and change calls. Independent plan groups execute concurrently over one MCP session. A verifier checks the candidate before finalization. With a fresh case database, it asks Jake's current floor, then whether the required client update was installed. Answer `2nd floor`, then `Yes`. Each answer resumes the existing LangGraph thread and is stored with human provenance. A second Jake case loads those confirmed facts before asking again.

The B99 enterprise records are **local fixtures**, including the WatchGuard alert and technician acknowledgement. They are never presented as live integrations. The timeline shows actual completed operations. The assessment separates hypotheses, confirmed facts, evidence support and uncertainty.

## API

| Method | Route | Purpose |
|---|---|---|
| GET | `/health` | Cached local runtime/storage status; no Azure request |
| GET | `/readiness` | Status-only configuration/local initialization; cloud checks remain unverified |
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

`CASE_DB_PATH` contains cases, confirmed facts and events. LangGraph uses the adjacent `<CASE_DB_PATH>.supervisor.checkpoints.sqlite3` file to avoid write contention with MCP operational memory. The previous case-agent checkpoint file is preserved; pre-upgrade paused cases are shown as historical and require a new investigation. Back up operational and checkpoint files together while the application is stopped. Chroma stays at version 0.5.3, preserving the existing storage format.

Verified Azure embeddings use `chunks_azure_<configuration hash>`; isolated test vectors use `chunks_stub_v1`. Previous local embedding collections are preserved. Legacy `chunks` lacks reliable embedding provenance and remains untouched. Re-ingest original source documents into the verified collection to make that knowledge available; no automatic destructive migration occurs. SHA-256 hashes and deterministic chunk IDs prevent exact duplicate storage across process restarts.

```bash
.venv/bin/python -m pytest backend/app/tests -q
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend run test:e2e
.venv/bin/python -m pip check
```

Playwright starts isolated servers on ports 8011/5184 with temporary SQLite/Chroma directories. Its backend uses deterministic test providers; browser requests use real HTTP/MCP; explicit layout fixtures mock only the stress-test data. Vitest only discovers `src/**/*.test.{ts,tsx}`. Optional Aura tests are skipped unless explicitly enabled with isolated credentials.

The isolated launcher explicitly selects deterministic providers and fresh stores:
`./scripts/fake-voice-demo.sh`. It displays **ISOLATED DEMO**; text is the intended
path. **Test microphone** sends PCM through the fake transport but has no recognition
or audible speech. Normal `make dev` uses configured Azure providers and never
falls back to this demo. Missing Azure configuration leaves diagnostics available
and generation actions disabled. `.venv/bin/python -m
backend.app.diagnostics.azure_readiness` checks local MCP/stores without Azure;
see the setup guide before using its explicit `--live` opt-in.
