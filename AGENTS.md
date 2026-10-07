# DeskMate architecture

DeskMate is a service-desk RAG application where the application layer calls retrieval through a real MCP protocol boundary.

## Runtime architecture

- React client + FastAPI backend
- Ask/Application Service receives `/ask`
- Planner decides retrieval strategy (`VECTOR`, `GRAPH`, `HYBRID`)
- MCP client calls the DeskMate MCP server over stdio
- DeskMate MCP server exposes the `search_knowledge` tool
- MCP server calls the existing Retriever; retrieval logic is not duplicated
- Existing Retriever uses Chroma and optional Neo4j Aura
- Responder builds the grounded prompt and calls the hosted model or stub provider
- Chroma remains the local persistent vector store
- Neo4j Aura remains optional for graph-enhanced retrieval
- MCP is a protocol boundary only; it is not the database, the LLM, or Chroma

## Actual implementation

- MCP server: `backend/app/mcp/server.py`
- MCP client: `backend/app/mcp/client.py`
- MCP transport: stdio via the official `mcp` Python SDK
- MCP tool currently exposed: `search_knowledge`
- `/ask` path: planner → MCP client → MCP server → existing Retriever → Chroma / optional Aura → responder → hosted model

## Supported runtime flow

React
↓
FastAPI
↓
Planner
↓
MCP Client
↓
DeskMate MCP Server
↓
Existing Retriever
├── Chroma
└── optional Neo4j Aura
↓
Responder
↓
Hosted LLM / stub

## Commands

Start the MCP server:

```bash
PYTHONPATH=. python -m backend.app.mcp.server
```

Start FastAPI:

```bash
PYTHONPATH=. uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

## Notes

- Retrieval behavior remains in the existing Retriever; it is reused through MCP.
- FastAPI must make a real MCP protocol call.
- No new infrastructure is introduced beyond the MCP boundary itself.
- Local or cloud graph behavior remains unchanged: Aura is optional; vector-only mode remains the fallback when Aura is absent.
