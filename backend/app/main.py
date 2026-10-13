"""Case API, ingestion endpoints and a lifespan-owned MCP/SQLite graph runtime."""

from __future__ import annotations
import uuid
import asyncio
from typing import Any
from collections.abc import Awaitable, Callable
from starlette.responses import Response
import base64
import secrets
from pydantic import SecretStr
from contextlib import asynccontextmanager
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from backend.app.agent.graph import ServiceDeskAgent
from backend.app.core.config import Settings, get_settings
from backend.app.diagnostics.readiness import ReadinessReport
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
    AskResponse,
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
    if s.voice_session_signing_key is None:
        s = s.model_copy(update={"voice_session_signing_key": SecretStr(secrets.token_urlsafe(48))})

    @asynccontextmanager
    async def lifespan(app):
        mcp = DeskMateMCPClient(s)
        app.state.settings, app.state.mcp = s, mcp
        voice_manager = None
        provider = None
        try:
            try:
                provider = get_provider(s)
            except LLMError:
                # Readiness remains available; generation routes reject configuration
                # errors explicitly. No fake provider is constructed as a fallback.
                provider = None
            await mcp.start()
            # Checkpoint infrastructure may precede the first MCP storage call.
            s.case_db_path.parent.mkdir(parents=True, exist_ok=True)
            async with AsyncSqliteSaver.from_conn_string(
                str(s.case_db_path) + ".supervisor.checkpoints.sqlite3"
            ) as saver:
                await saver.setup()
                app.state.agent = ServiceDeskAgent(s, mcp, provider, saver)
                await app.state.agent.initialize()
                from backend.app.voice.service import VoiceManager

                voice_manager = VoiceManager(s, app.state.agent.harness, provider)
                app.state.voice = voice_manager
                app.state.provider_initialized = provider is not None
                try:
                    local = await app.state.agent.harness.invoke("get_local_storage_status", {})
                    app.state.local_storage = local["result"]
                except (MCPToolError, MCPConnectionError, StorageError):
                    app.state.local_storage = {
                        "chroma": False,
                        "sqlite": False,
                        "aura_connected": False,
                    }
                yield
        finally:
            if voice_manager:
                await voice_manager.close()
            await mcp.close()
            if provider and hasattr(provider, "close"):
                await asyncio.wait_for(asyncio.to_thread(provider.close), 5)

    app = FastAPI(title="DeskMate Case API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )
    from backend.app.voice.api import register_voice_routes

    register_voice_routes(app)

    @app.middleware("http")
    async def protect_voice_cases(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path.startswith(("/cases/voice-", "/requests/voice-")):
            from backend.app.voice.auth import COOKIE_NAME, VoiceError, verify_actor
            from fastapi.responses import JSONResponse

            try:
                from backend.app.voice.api import browser_guard

                browser_guard(request, s)
                actor = verify_actor(s, request.cookies.get(COOKIE_NAME))
                session_id = request.url.path.split("/")[2].removeprefix("voice-")
                app.state.voice.repository.get(session_id, actor)
            except VoiceError as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})
        return await call_next(request)

    def readiness() -> ReadinessReport:
        from backend.app.diagnostics.readiness import configuration_readiness

        mcp = app.state.mcp
        connected = mcp._task is not None and not mcp._task.done()
        stores = app.state.local_storage
        return configuration_readiness(
            s,
            reasoning_initialized=app.state.provider_initialized,
            local_mcp="READY" if connected else "ERROR",
            private_stores="READY" if stores["chroma"] and stores["sqlite"] else "ERROR",
        )

    @app.get("/readiness")
    async def get_readiness() -> dict[str, Any]:
        # No token acquisition/network probe; statuses and missing variable names only.
        return readiness().model_dump()

    @app.get("/health", response_model=HealthResponse)
    async def health():
        report = readiness()
        environment = app.state.local_storage
        return HealthResponse(
            status="ok" if report.text_ready else "degraded",
            generation_provider=s.generation_provider,
            test_mode=s.test_mode,
            mcp_connected=report.local_mcp == "READY",
            chroma=environment["chroma"],
            aura_configured=bool(s.neo4j_uri),
            aura_connected=environment["aura_connected"],
            voice_enabled=s.voice_enabled,
            voice_provider=s.voice_provider,
            voicelive_configured=s.voice_provider == "azure_voicelive" and report.voice.configured,
            embedding_provider=s.embed_provider,
            azure_openai_configured=(
                s.generation_provider == "azure_openai" and report.reasoning.configured
            ),
            readiness=report.model_dump(),
        )

    def require_reasoning() -> None:
        report = readiness()
        if not report.text_ready:
            message = "Azure reasoning is not ready. Local case saving, document extraction and keyword search remain available."
            if report.azure_usage and not report.azure_usage["enabled"]:
                message = "Azure requests are disabled by the usage guard or emergency shutdown. Local case saving, document extraction and keyword search remain available."
            failure = report.azure_validation.get("text_workflow", {})
            if failure and not failure["passed"]:
                message += " Last Azure workflow failure: " + failure["code"] + "."
            raise HTTPException(
                503,
                {
                    "code": "AZURE_REASONING_NOT_READY",
                    "message": message,
                },
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
            # Distinguish parsing, policy and embedding failures.
            raise HTTPException(exc.status_code, str(exc)) from exc

    async def document_upload(file: UploadFile, title: str | None, tool: str):
        from pathlib import Path

        data = await file.read(5_000_001)
        if len(data) > 5_000_000:
            raise HTTPException(413, "Document exceeds 5 MB")
        suffix = Path(file.filename or "").suffix.lower()
        media_type = {".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/markdown"}.get(
            suffix
        )
        if media_type is None:
            raise HTTPException(400, "Choose a PDF, .txt or .md document")
        arguments = {"data_base64": base64.b64encode(data).decode(), "media_type": media_type}
        if tool == "save_document":
            arguments["title"] = title or file.filename
        return (await app.state.agent.harness.invoke(tool, arguments))["result"]

    @app.post("/documents/extract")
    async def extract_upload(file: UploadFile = File(...)):
        return await document_upload(file, None, "extract_document")

    @app.post("/documents/upload")
    async def save_upload(file: UploadFile = File(...), title: str | None = Form(None)):
        return await document_upload(file, title, "save_document")

    @app.post("/documents/paste")
    async def save_pasted_document(body: IngestPasteRequest):
        return (
            await app.state.agent.harness.invoke(
                "save_document",
                {
                    "data_base64": base64.b64encode(body.text.encode()).decode(),
                    "title": body.title,
                    "media_type": "text/plain",
                },
            )
        )["result"]

    @app.get("/documents")
    async def list_local_documents():
        return (await app.state.agent.harness.invoke("list_documents", {}))["items"]

    @app.post("/documents/{document_id}/index")
    async def index_local_document(document_id: str):
        report = readiness()
        if not report.semantic_ingestion_ready:
            raise HTTPException(
                503,
                {
                    "code": "AZURE_EMBEDDINGS_NOT_READY",
                    "message": report.semantic_ingestion_blocker,
                },
            )
        return (
            await app.state.agent.harness.invoke("index_document", {"document_id": document_id})
        )["result"]

    @app.get("/knowledge/search")
    async def search_local_knowledge(question: str):
        if not question.strip() or len(question) > 2000:
            raise HTTPException(422, "Search text must contain 1 to 2000 characters")
        result = await app.state.agent.harness.invoke(
            "search_knowledge", {"question": question, "mode": "KEYWORD", "top_k": 6}
        )
        return {"mode": "keyword", "generated_answer": False, "items": result["items"]}

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

    async def request_result(request_id, summary=None, answer=None):
        require_reasoning()
        try:
            state, events = await app.state.agent.run_request(request_id, summary, answer)
            return AskResponse(
                request_id=request_id,
                intent="knowledge_question" if state["workflow"] == "knowledge" else "service_desk",
                case_id=request_id if state.get("persist_case") else None,
                result=state,
                events=events,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (LLMError, MCPToolError, MCPConnectionError, StorageError) as exc:
            from fastapi.responses import JSONResponse

            stored = (await app.state.agent.harness.invoke("get_case", {"case_id": request_id}))[
                "result"
            ]
            return JSONResponse(
                status_code=502,
                content={"detail": str(exc), "case_id": request_id if stored else None},
            )

    @app.post("/ask", response_model=AskResponse)
    async def ask(body: CaseCreateRequest):
        return await request_result(uuid.uuid4().hex, body.summary)

    @app.post("/requests/{request_id}/resume", response_model=AskResponse)
    async def resume_request(request_id: str, body: CaseResumeRequest):
        return await request_result(request_id, answer=body.answer)

    @app.get("/cases", response_model=list[CaseResponse])
    async def list_cases(request: Request) -> list[CaseResponse]:
        from backend.app.voice.auth import COOKIE_NAME, VoiceError, verify_actor

        def visible(state: dict[str, Any]) -> bool:
            if not state["case_id"].startswith("voice-"):
                return True
            try:
                actor = verify_actor(s, request.cookies.get(COOKIE_NAME))
                app.state.voice.repository.get(state["case_id"].removeprefix("voice-"), actor)
                return True
            except VoiceError:
                return False

        return [
            case_response(state)
            for state in (await app.state.agent.harness.invoke("list_cases", {}))["items"]
            if visible(state)
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
        require_reasoning()
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

        return JSONResponse(status_code=getattr(exc, "status_code", 502), content={"detail": str(exc)})

    @app.exception_handler(UnsafeURL)
    @app.exception_handler(FetchError)
    async def web_error(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


app = create_app()
