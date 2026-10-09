"""Case API, ingestion endpoints and a lifespan-owned MCP/SQLite graph runtime."""

from __future__ import annotations
import uuid
import base64
from contextlib import asynccontextmanager
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from backend.app.agent.graph import ServiceDeskAgent
from backend.app.core.config import Settings, get_settings
from backend.app.mcp.client import DeskMateMCPClient, MCPToolError, MCPConnectionError
from backend.app.models.dto import (
    CaseCreateRequest,
    CaseResumeRequest,
    CaseResponse,
    CaseEvent,
    HealthResponse,
    IngestPasteRequest,
    IngestPasteResponse,
    IngestPdfResponse,
    IngestUrlRequest,
    IngestUrlResponse,
)
from backend.app.models.provider import LLMError
from backend.app.models.provider_factory import get_provider
from backend.app.services.web import UnsafeURL, FetchError
from backend.app.store.vector_chroma import StorageError


def case_response(state):
    data = {**state, "thread_id": state["case_id"]}
    if (state.get("status") == "needs_input" and not state.get("workflow")) or state.get(
        "status"
    ) not in {
        "new",
        "investigating",
        "needs_input",
        "assessment_ready",
        "insufficient_information",
        "error",
    }:
        # Preserve historical rows on disk, while avoiding unsupported old claims
        # and questions that have no LangGraph interrupt checkpoint to resume.
        data.update(
            status="insufficient_information",
            pending_question=None,
            confidence=0,
            final_answer=None,
            evidence=[],
            uncertainty=[
                "Historical case has no compatible supervisor checkpoint. Start a new investigation from its ticket."
            ],
        )
    return CaseResponse.model_validate(data)


def create_app(settings: Settings | None = None):
    s = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app):
        mcp = DeskMateMCPClient(s)
        app.state.settings, app.state.mcp = s, mcp
        try:
            provider = get_provider(s)
            await mcp.start()
            # Checkpoint infrastructure may precede the first MCP storage call.
            s.case_db_path.parent.mkdir(parents=True, exist_ok=True)
            async with AsyncSqliteSaver.from_conn_string(
                str(s.case_db_path) + ".supervisor.checkpoints.sqlite3"
            ) as saver:
                await saver.setup()
                app.state.agent = ServiceDeskAgent(s, mcp, provider, saver)
                await app.state.agent.initialize()
                yield
        finally:
            await mcp.close()

    app = FastAPI(title="DeskMate Case API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.allowed_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/health", response_model=HealthResponse)
    async def health():
        environment = (await app.state.agent.harness.invoke("list_available_context", {}))["result"]
        return HealthResponse(
            status="ok",
            generation_provider=s.generation_provider,
            test_mode=s.test_mode,
            mcp_connected=app.state.mcp._task is not None and not app.state.mcp._task.done(),
            chroma=environment["chroma"],
            aura_configured=bool(s.neo4j_uri),
            aura_connected=environment["aura_connected"],
        )

    @app.post("/ingest/paste", response_model=IngestPasteResponse)
    async def ingest_paste(body: IngestPasteRequest):
        return (
            await app.state.agent.harness.invoke(
                "ingest_text", {"title": body.title, "text": body.text}
            )
        )["result"]

    @app.post("/ingest/pdf", response_model=IngestPdfResponse)
    async def ingest_pdf(file: UploadFile = File(...), title: str | None = Form(None)):
        data = await file.read(5_000_001)
        if len(data) > 5_000_000:
            raise HTTPException(413, "PDF exceeds 5 MB")
        if not data.startswith(b"%PDF-"):
            raise HTTPException(400, "A valid PDF is required")
        try:
            return (
                await app.state.agent.harness.invoke(
                    "ingest_document",
                    {
                        "title": title or file.filename,
                        "data_base64": base64.b64encode(data).decode(),
                        "media_type": "application/pdf",
                    },
                )
            )["result"]
        except MCPToolError as exc:
            raise HTTPException(400, "PDF could not be parsed") from exc

    @app.post("/ingest/url", response_model=IngestUrlResponse)
    async def ingest_url(body: IngestUrlRequest):
        if not s.allow_url_ingest:
            raise HTTPException(403, "URL ingestion is disabled")
        return (
            await app.state.agent.harness.invoke("ingest_url", body.model_dump(exclude_none=True))
        )["result"]

    async def load(case_id):
        state = (await app.state.agent.harness.invoke("get_case", {"case_id": case_id}))["result"]
        if state is None:
            raise HTTPException(404, "Case not found")
        return state

    @app.get("/cases", response_model=list[CaseResponse])
    async def list_cases():
        return [
            case_response(state)
            for state in (await app.state.agent.harness.invoke("list_cases", {}))["items"]
        ]

    @app.post("/cases", response_model=CaseResponse, status_code=201)
    async def create_case(body: CaseCreateRequest):
        case_id = uuid.uuid4().hex
        state = {
            "case_id": case_id,
            "ticket": {"summary": body.summary},
            "status": "new",
            "confidence": 0,
        }
        await app.state.agent.harness.invoke("save_case", {"case_id": case_id, "state": state})
        await app.state.agent.note(state, "create_case", "Case created", "human_input")
        return case_response(state)

    @app.get("/cases/{case_id}", response_model=CaseResponse)
    async def get_case(case_id: str):
        return case_response(await load(case_id))

    async def run(case_id, answer=None):
        await load(case_id)
        try:
            return case_response(await app.state.agent.run_case(case_id, answer))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (LLMError, MCPToolError, MCPConnectionError, StorageError) as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.post("/cases/{case_id}/investigate", response_model=CaseResponse)
    async def investigate(case_id: str):
        return await run(case_id)

    @app.post("/cases/{case_id}/resume", response_model=CaseResponse)
    async def resume(case_id: str, body: CaseResumeRequest):
        return await run(case_id, body.answer)

    @app.get("/cases/{case_id}/events", response_model=list[CaseEvent])
    async def events(case_id: str):
        await load(case_id)
        return (await app.state.agent.harness.invoke("get_case_history", {"case_id": case_id}))[
            "items"
        ]

    @app.exception_handler(MCPToolError)
    @app.exception_handler(StorageError)
    @app.exception_handler(RuntimeError)
    async def storage_error(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(UnsafeURL)
    @app.exception_handler(FetchError)
    async def web_error(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


app = create_app()
