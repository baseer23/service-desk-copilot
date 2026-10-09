# DeskMate frontend

React and TypeScript knowledge and case-investigation interface for the DeskMate FastAPI Case API.

```bash
npm ci
npm run dev -- --host 127.0.0.1
```

Open http://127.0.0.1:5173 with the backend running on port 8000. Vite proxies `/cases`, `/health` and `/ingest`; `API_PROXY_TARGET` changes its backend target. `VITE_API_BASE` sets an explicit API origin when building for another hosting arrangement.

Enter a question or ticket and click **Run DeskMate**. The supervisor chooses the workflow. Knowledge requests show an answer, sources and research summary. Cases show hypotheses, confirmed facts, inferences, contradictions, a material human question, next action and evidence support. Agent activity is collapsible, with actual tools used; source content is readable instead of raw JSON. The interface creates cases, renders actual events, asks for missing human input, resumes the same case and displays assessment, confirmed facts, remembered facts and source provenance. Text, PDF and public URL ingestion remain available. Fixture evidence is labeled visibly.

```bash
npm test
npm run build
npm run test:e2e
```

Vitest discovers only source unit tests. Playwright starts isolated backend/frontend servers on 8011/5184 with fresh temporary storage and test providers. It exercises the complete Jake flow and a second case with cross-case facts using real HTTP. Ports must be free; it will not reuse unrelated servers. Backend setup uses the repository `.venv` described in `../docs/README.md`.
