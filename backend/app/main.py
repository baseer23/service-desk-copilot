import logging
import logging.config
import sys
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

from backend.app.adapters.embeddings import StubEmbeddingProvider, get_embedding_provider
from backend.app.core.config import get_settings
from backend.app.models.dto import (
    AskRequest,
    AskResponse,
    IngestPasteRequest,
    IngestPasteResponse,
    IngestPdfResponse,
)
from backend.app.models.provider_factory import get_provider
from backend.app.rag.answer import Responder
from backend.app.rag.planner import Planner
from backend.app.rag.retrieve import Retriever
from backend.app.services.ingest_service import IngestService
from backend.app.store.graph_repo import GraphRepository, InMemoryGraphRepository
from backend.app.store.vector_chroma import InMemoryVectorStore, VectorChromaStore

try:  # pragma: no cover - optional dependency for Neo4j
    from neo4j import GraphDatabase
except ImportError:  # pragma: no cover
    GraphDatabase = None  # type: ignore

SETTINGS = get_settings()
LOGGING_CONFIG = Path(__file__).resolve().parents[1] / "logging.ini"
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
MAX_BODY_BYTES = 1024 * 1024
MAX_INGEST_BYTES = 5 * 1024 * 1024

if LOGGING_CONFIG.exists():
    logging.config.fileConfig(
        LOGGING_CONFIG,
        disable_existing_loggers=False,
        defaults={"sys": sys},
    )
else:  # pragma: no cover - fallback logging configuration
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

logger = logging.getLogger("service-desk")

app = FastAPI(title=SETTINGS.app_name)
app.state.vector_store = InMemoryVectorStore()
app.state.graph_repo = InMemoryGraphRepository()
app.state.graph_driver = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=SETTINGS.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _init_vector_store(settings):
    try:
        return VectorChromaStore(path=str(settings.chroma_dir))
    except Exception as exc:  # pragma: no cover - fallback path
        logger.warning("Chroma unavailable (%s); using in-memory vector store", exc)
        return InMemoryVectorStore()


def _init_graph_repo(settings):
    if GraphDatabase is None:  # pragma: no cover - driver not installed
        logger.warning("neo4j driver not installed; using in-memory graph store")
        return InMemoryGraphRepository(), None
    try:
        driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
        driver.verify_connectivity()
        repo = GraphRepository(driver)
        repo.ensure_constraints()
        return repo, driver
    except Exception as exc:  # pragma: no cover - handle offline graph
        logger.warning("Neo4j unavailable (%s); using in-memory graph store", exc)
        return InMemoryGraphRepository(), None


@app.on_event("startup")
async def startup_event():  # pragma: no cover - exercised in integration tests
    settings = get_settings()
    app.state.settings = settings
    app.state.vector_store = _init_vector_store(settings)
    repo, driver = _init_graph_repo(settings)
    app.state.graph_repo = repo
    app.state.graph_driver = driver


@app.on_event("shutdown")
async def shutdown_event():  # pragma: no cover - exercised in integration tests
    driver = getattr(app.state, "graph_driver", None)
    if driver:
        driver.close()


class SpaStaticFiles(StaticFiles):
    """Serve SPA assets while falling back to index.html for unknown routes."""

    async def get_response(self, path: str, scope):  # type: ignore[override]
        try:
            return await super().get_response(path, scope)
        except Exception:  # pragma: no cover - relies on filesystem state
            index_file = Path(self.directory) / "index.html"
            if index_file.exists():
                return FileResponse(index_file)
            return JSONResponse({"detail": "Not Found"}, status_code=404)


@app.middleware("http")
async def enforce_body_limit(request: Request, call_next: Callable):
    protected_paths = {"/ask", "/ingest/paste"}
    if request.method.upper() == "POST" and request.url.path in protected_paths:
        content_length = request.headers.get("content-length")
        limit = MAX_BODY_BYTES if request.url.path == "/ask" else MAX_INGEST_BYTES
        if content_length:
            try:
                if int(content_length) > limit:
                    return JSONResponse({"detail": "Payload too large"}, status_code=413)
            except ValueError:
                pass
        else:
            body = await request.body()
            if len(body) > limit:
                return JSONResponse({"detail": "Payload too large"}, status_code=413)
            request._body = body  # type: ignore[attr-defined]
    response = await call_next(request)
    return response


@app.middleware("http")
async def log_requests(request: Request, call_next: Callable):
    response = await call_next(request)
    logger.info("%s %s -> %s", request.method, request.url.path, response.status_code)
    return response


@app.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "provider": settings.model_provider}


@app.post("/ingest/paste", response_model=IngestPasteResponse)
def ingest_paste(request: IngestPasteRequest):
    if len(request.text.encode("utf-8")) > MAX_INGEST_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    settings = get_settings()
    embedding_provider = _safe_embedding_provider(settings)
    service = IngestService(
        settings=settings,
        vector_store=app.state.vector_store,
        graph_repo=app.state.graph_repo,
        embedding_provider=embedding_provider,
    )
    response = service.ingest_text(request.title, request.text)
    return response


@app.post("/ingest/pdf", response_model=IngestPdfResponse)
async def ingest_pdf(file: UploadFile = File(...), title: Optional[str] = None):
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="Unsupported file type")
    content = await file.read()
    if len(content) > MAX_INGEST_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    settings = get_settings()
    embedding_provider = _safe_embedding_provider(settings)
    service = IngestService(
        settings=settings,
        vector_store=app.state.vector_store,
        graph_repo=app.state.graph_repo,
        embedding_provider=embedding_provider,
    )
    try:
        result = service.ingest_pdf(title or file.filename, content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to process PDF: {exc}") from exc
    return result


@app.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest) -> AskResponse:
    settings = get_settings()
    planner = Planner(settings=settings, graph_repo=app.state.graph_repo)
    plan = planner.plan(payload.question)
    top_k = payload.top_k or plan.get("top_k", settings.top_k)

    embedding_provider = _safe_embedding_provider(settings)
    retriever = Retriever(
        settings=settings,
        vector_store=app.state.vector_store,
        graph_repo=app.state.graph_repo,
        embedding_provider=embedding_provider,
    )

    mode = plan.get("mode", "VECTOR").upper()
    if mode == "GRAPH":
        contexts = retriever.graph_search(payload.question, top_k)
        if not contexts:
            contexts = retriever.vector_search(payload.question, top_k)
    elif mode == "HYBRID":
        contexts = retriever.hybrid_search(payload.question, top_k)
    else:
        contexts = retriever.vector_search(payload.question, top_k)

    provider = get_provider()
    responder = Responder(settings=settings, provider=provider)
    response = responder.answer(payload.question, plan, contexts)
    return response


def _safe_embedding_provider(settings):
    try:
        return get_embedding_provider(settings)
    except Exception as exc:
        logger.warning("Falling back to stub embeddings (%s)", exc)
        return StubEmbeddingProvider()


if FRONTEND_DIST.exists():
    app.mount("/", SpaStaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
