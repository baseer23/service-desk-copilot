# Voice input is continuous Speech-to-Text

The current implementation uses Azure AI Speech SDK continuous recognition. Responses are text. Azure Voice Live, realtime audio-model inference and speech synthesis have been removed.

See [SPEECH.md](SPEECH.md) for microphone selection, partial/final transcripts, standalone Stop detection, reconnection, usage accounting, configuration and honest live acceptance criteria. The Voice tab submits recognized text through the same selected Groq/Azure OpenAI LangGraph workflow as chat.

Customize reasoning instructions in `backend/app/models/instructions.py`. Changes to Python prompt code require restarting FastAPI (`make dev` restart); selecting another provider does not. Speech recognition itself has no speaking personality or response-generation prompt.
