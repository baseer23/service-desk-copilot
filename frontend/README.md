# Service Desk Copilot Frontend

Single-page Vite + React (TypeScript) UI that speaks to the FastAPI backend. Sprint 2 adds an ingestion panel, hybrid RAG controls, and citation viewing in the chat stream.

## Setup
```bash
npm install
npm run dev
```

By default the app calls the backend on `http://localhost:8000`. Override during development with:
```bash
VITE_API_BASE=http://localhost:8000 npm run dev
```

## Features
- **Ingest panel** (Paste | PDF) for uploading knowledge locally. Success feedback shows chunk/entity/vector counts.
- **Chat** section with a ChatGPT-style layout. Messages are *not* persisted across refresh.
- **Citations** displayed beneath assistant replies; expand to read the relevant chunk text.
- **Recent questions** sidebar tracks the last five prompts for quick reference.

Use Enter to send, Shift+Enter for a newline. A pending state (“Thinking…”) mirrors server latency while the backend plans, retrieves, and synthesizes.

## Build for production
```bash
npm run build
```
The compiled assets land in `frontend/dist` and are served automatically by the FastAPI app when present.
