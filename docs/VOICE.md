# Voice input is continuous Speech-to-Text

The current implementation uses Azure AI Speech SDK continuous recognition. Responses are text. Azure Voice Live, realtime audio-model inference and speech synthesis have been removed.

See [SPEECH.md](SPEECH.md) for microphone selection, partial/final transcripts, standalone Stop detection, reconnection, usage accounting, configuration and honest live acceptance criteria. The Voice tab submits recognized text through the same selected Groq/Azure OpenAI LangGraph workflow as chat.

Customize reasoning instructions in `backend/app/models/instructions.py`. Changes to Python prompt code require restarting FastAPI (`make dev` restart); selecting another provider does not. Speech recognition itself has no speaking personality or response-generation prompt.

## Turn-based conversation repair — 2026-10-15

The Voice tab starts with the Digital nGenuity greeting and uses a dedicated helpdesk conversation session. Stopping capture waits for Azure Speech SDK closure and its final-event drain before submitting one combined transcript. The frontend proxy includes the voice routes, and unavailable MCP capabilities do not gate ordinary voice chat.

Executed checks: **317 backend tests passed and two optional Aura tests skipped; 52 frontend tests passed; the production build passed; two focused Playwright Voice scenarios passed.** One minimal real Groq turn and one minimal real Azure OpenAI turn both returned relevant responses through the frontend proxy while retaining the same session. The full Playwright run passed 16 of 19; the two repaired Voice scenarios passed on focused rerun, while one unrelated pre-existing two-interrupt investigation scenario timed out. A fresh live Azure Speech request was not executed because the available prerecorded audio was not approved for external submission. Physical microphone recognition therefore remains a manual check; browser capture, state transitions, SDK final-flush ordering, confirmed closure and duplicate suppression were verified without claiming live microphone recognition.
