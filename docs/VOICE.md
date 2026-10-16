# Voice input is continuous Speech-to-Text

The current implementation uses Azure AI Speech SDK continuous recognition. Responses are text. Azure Voice Live, realtime audio-model inference and speech synthesis have been removed.

See [SPEECH.md](SPEECH.md) for microphone selection, partial/final transcripts, standalone Stop detection, reconnection, usage accounting, configuration and honest live acceptance criteria. The Voice tab submits recognized text through the same selected Groq/Azure OpenAI LangGraph workflow as chat.

Customize reasoning instructions in `backend/app/models/instructions.py`. Changes to Python prompt code require restarting FastAPI (`make dev` restart); selecting another provider does not. Speech recognition itself has no speaking personality or response-generation prompt.

## Turn-based conversation repair — 2026-10-15

The Voice tab starts with the Digital nGenuity greeting and uses a dedicated helpdesk conversation session. Stopping capture waits for Azure Speech SDK closure and its final-event drain before submitting one combined transcript. The frontend proxy includes the voice routes, and unavailable MCP capabilities do not gate ordinary voice chat.

Executed checks: **317 backend tests passed and two optional Aura tests skipped; 52 frontend tests passed; the production build passed; two focused Playwright Voice scenarios passed.** One minimal real Groq turn and one minimal real Azure OpenAI turn both returned relevant responses through the frontend proxy while retaining the same session. The full Playwright run passed 16 of 19; the two repaired Voice scenarios passed on focused rerun, while one unrelated pre-existing two-interrupt investigation scenario timed out. A fresh live Azure Speech request was not executed because the available prerecorded audio was not approved for external submission. Physical microphone recognition therefore remains a manual check; browser capture, state transitions, SDK final-flush ordering, confirmed closure and duplicate suppression were verified without claiming live microphone recognition.

## Stateful email correction — 2026-10-16

Email-like turns now enter a structured, versioned correction session while unrelated helpdesk turns remain general conversation. `backend/config/approved_email_domains.txt` is the sole domain authority. The local MCP resolver performs exact, normalized and conservative RapidFuzz matching, preserves explicit top-level domains, reports ambiguity, and supports validated reloads. A resolved domain is locked independently from username edits; undo, idempotency keys, optimistic versions and session expiry prevent lost or repeated corrections. Azure Speech receives a conservative phrase list derived from the same approved file and still closes between turns.

The exact six-turn reported transcript reached `abdulbaseer@ngenuity.ca`; every intermediate state was emitted by the regression. Verification: **319 backend tests passed with two optional Aura skips, 52 frontend tests passed, the production build passed, pip check passed, and two focused Voice Playwright scenarios passed.** A synthetic live MCP/domain turn succeeded. Azure OpenAI successfully returned and applied the structured clarification while retaining the locked domain. The single Groq structured-correction request returned invalid structured output; it was not retried because the request may have been billable, and the existing email state remained unchanged. No new live Azure Speech or physical microphone accuracy test was performed.
