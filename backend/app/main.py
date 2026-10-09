"""Case API, ingestion endpoints and a lifespan-owned MCP/SQLite graph runtime."""

from __future__ import annotations
import asyncio
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from backend.app.agent.graph import ServiceDeskAgent
from backend.app.agent.memory import CaseMemoryStore
from backend.app.adapters.embeddings import get_embedding_provider
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
from backend.app.services.capabilities import knowledge_stores
from backend.app.services.ingest_service import IngestService
from backend.app.services.url_crawler import UrlCrawler, CrawlLimits
from backend.app.services.web import UnsafeURL, FetchError
from backend.app.store.vector_chroma import StorageError


def case_response(state):
    data = {**state, "thread_id": state["case_id"]}
    if state.get("status") not in {
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
                "Historical case has no durable investigation checkpoint. Start a new investigation from its ticket."
            ],
        )
    return CaseResponse.model_validate(data)


def create_app(settings: Settings | None = None):
    s = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app):
        memory = CaseMemoryStore(s.case_db_path)
        vector, graph, driver = knowledge_stores(s)
        ingest = IngestService(s, vector, graph, get_embedding_provider(s))
        mcp = DeskMateMCPClient(s)
        app.state.settings, app.state.memory = s, memory
        app.state.vector_store, app.state.graph_repo, app.state.graph_driver = vector, graph, driver
        app.state.ingest, app.state.mcp = ingest, mcp
        try:
            provider = get_provider(s)
            await mcp.start()
            # Checkpoint writes use their own SQLite file, avoiding contention
            # with operational facts/events written by the MCP process.
            async with AsyncSqliteSaver.from_conn_string(
                str(s.case_db_path) + ".checkpoints.sqlite3"
            ) as saver:
                await saver.setup()
                app.state.agent = ServiceDeskAgent(s, mcp, memory, provider, saver, ingest)
                yield
        finally:
            await mcp.close()
            if driver:
                driver.close()

    app = FastAPI(title="DeskMate Case API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.allowed_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/health", response_model=HealthResponse)
    async def health():
        return HealthResponse(
            status="ok",
            generation_provider=s.generation_provider,
            test_mode=s.test_mode,
            mcp_connected=app.state.mcp._task is not None and not app.state.mcp._task.done(),
            chroma=app.state.vector_store.ping(),
            aura_configured=bool(s.neo4j_uri),
            aura_connected=app.state.graph_driver is not None,
        )

    @app.post("/ingest/paste", response_model=IngestPasteResponse)
    async def ingest_paste(body: IngestPasteRequest):
        return await asyncio.to_thread(app.state.ingest.ingest_text, body.title, body.text)

    @app.post("/ingest/pdf", response_model=IngestPdfResponse)
    async def ingest_pdf(file: UploadFile = File(...), title: str | None = Form(None)):
        data = await file.read(5_000_001)
        if len(data) > 5_000_000:
            raise HTTPException(413, "PDF exceeds 5 MB")
        if not data.startswith(b"%PDF-"):
            raise HTTPException(400, "A valid PDF is required")
        try:
            return await asyncio.to_thread(
                app.state.ingest.ingest_pdf, title or file.filename, data
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, "PDF could not be parsed") from exc

    @app.post("/ingest/url", response_model=IngestUrlResponse)
    async def ingest_url(body: IngestUrlRequest):
        if not s.allow_url_ingest:
            raise HTTPException(403, "URL ingestion is disabled")
        limits = CrawlLimits(
            body.max_depth if body.max_depth is not None else s.url_max_depth,
            body.max_pages if body.max_pages is not None else s.url_max_pages,
            s.url_max_total_chars,
            s.url_rate_limit_sec,
        )
        result = await asyncio.to_thread(UrlCrawler(s).crawl, body.url, limits)
        totals = {"chunks": 0, "entities": 0, "vector_count": 0, "ms": 0}
        for page in result.pages:
            ingested = await asyncio.to_thread(
                app.state.ingest.ingest_text,
                page.title,
                page.content,
                source=page.url,
                source_type="url",
                url=page.url,
            )
            for key in totals:
                totals[key] += getattr(ingested, key)
        return IngestUrlResponse(
            **totals, pages=len(result.pages), skipped_urls=result.skipped_urls
        )

    def load(case_id):
        state = app.state.memory.load_case(case_id)
        if state is None:
            raise HTTPException(404, "Case not found")
        return state

    @app.get("/cases", response_model=list[CaseResponse])
    async def list_cases():
        return [case_response(load(cid)) for cid in app.state.memory.list_cases()]

    @app.post("/cases", response_model=CaseResponse, status_code=201)
    async def create_case(body: CaseCreateRequest):
        case_id = uuid.uuid4().hex
        state = {
            "case_id": case_id,
            "ticket": {"summary": body.summary},
            "status": "new",
            "confidence": 0,
        }
        app.state.memory.save_case(case_id, state)
        app.state.memory.record_event(case_id, "create_case", "human_input", "Case created")
        return case_response(state)

    @app.get("/cases/{case_id}", response_model=CaseResponse)
    async def get_case(case_id: str):
        return case_response(load(case_id))

    async def run(case_id, answer=None):
        load(case_id)
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
        load(case_id)
        return app.state.memory.list_events(case_id)

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
