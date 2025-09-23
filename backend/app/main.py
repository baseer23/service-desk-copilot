import logging
import logging.config
import sys
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

import requests

from backend.app.adapters.embeddings import StubEmbeddingProvider, get_embedding_provider
from backend.app.core.config import get_settings
from backend.app.models.dto import (
    AskRequest,
    AskResponse,
    IngestPasteRequest,
    IngestPasteResponse,
    IngestPdfResponse,
)
from backend.app.models.provider_factory import (
    ProviderContext,
    SMALL_OLLAMA_MODELS,
    get_provider,
    select_provider,
)
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
_initial_provider_context = select_provider(SETTINGS)
app.state.provider = _initial_provider_context.provider
app.state.provider_context = _initial_provider_context
app.state.settings = SETTINGS

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
    provider_context = select_provider(settings)
    app.state.provider = provider_context.provider
    app.state.provider_context = provider_context


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
def health() -> dict[str, object]:
    settings = getattr(app.state, "settings", SETTINGS)
    provider_context = getattr(app.state, "provider_context", select_provider(settings))
    provider = provider_context.provider
    vector_path, vector_exists = _vector_store_state(getattr(app.state, "vector_store", None), settings)

    return {
        "status": "ok",
        "provider": provider.name(),
        "provider_type": provider_context.provider_type,
        "model_name": provider_context.model_name,
        "provider_vendor": provider_context.vendor,
        "local_model_available": provider_context.local_model_available,
        "operator_message": provider_context.reason,
        "hosted_reachable": _probe_hosted(settings) if provider_context.provider_type == "hosted" else None,
        "hosted_model_name": getattr(settings, "hosted_model_name", None),
        "preferred_local_models": [choice for choice, _ in SMALL_OLLAMA_MODELS],
        "ollama_reachable": _probe_ollama(settings),
        "llamacpp_reachable": _probe_llamacpp(settings),
        "neo4j_reachable": _probe_neo4j(getattr(app.state, "graph_repo", None)),
        "vector_store_path": vector_path,
        "vector_store_path_exists": vector_exists,
    }


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
    settings = getattr(app.state, "settings", SETTINGS)
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

    context = getattr(app.state, "provider_context", select_provider(settings))
    provider = context.provider
    responder = Responder(settings=settings, provider=provider)
    response = responder.answer(payload.question, plan, contexts)
    return response


def _safe_embedding_provider(settings):
    try:
        return get_embedding_provider(settings)
    except Exception as exc:
        logger.warning("Falling back to stub embeddings (%s)", exc)
        return StubEmbeddingProvider()


def _probe_hosted(settings) -> bool | None:
    api_key = getattr(settings, "groq_api_key", None)
    api_url = getattr(settings, "groq_api_url", "")
    if not api_key or not api_url:
        return False
    url = _groq_models_url(api_url)
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        response = requests.get(url, headers=headers, timeout=1)
        response.raise_for_status()
        return True
    except requests.RequestException:  # pragma: no cover - network-dependent
        return False


def _groq_models_url(api_url: str) -> str:
    if not api_url:
        return ""
    sanitized = api_url.rstrip("/")
    suffix = "/chat/completions"
    if sanitized.endswith(suffix):
        sanitized = sanitized[: -len(suffix)]
    if not sanitized.endswith("/openai/v1"):
        parts = sanitized.split("/openai/v1", 1)
        base = parts[0] if len(parts) > 1 else sanitized
        return f"{base.rstrip('/')}/openai/v1/models"
    return f"{sanitized}/models"


def _probe_ollama(settings) -> bool:
    host = getattr(settings, "ollama_host", "http://localhost:11434")
    url = f"{host.rstrip('/')}/api/tags"
    try:
        response = requests.get(url, timeout=1)
        response.raise_for_status()
        return True
    except requests.RequestException:  # pragma: no cover - network-dependent
        return False


def _probe_llamacpp(settings) -> bool:
    host = getattr(settings, "llamacpp_host", "http://localhost:8080")
    url = f"{host.rstrip('/')}/health"
    try:
        response = requests.get(url, timeout=1)
        response.raise_for_status()
        return True
    except requests.RequestException:  # pragma: no cover - network-dependent
        return False


def _probe_neo4j(graph_repo) -> bool:
    if graph_repo is None:
        return False
    ping = getattr(graph_repo, "ping", None)
    if callable(ping):
        try:
            return bool(ping())
        except Exception:  # pragma: no cover - defensive fallback
            return False
    return False


def _vector_store_state(vector_store, settings) -> Tuple[str, bool]:
    if vector_store is not None and hasattr(vector_store, "path"):
        raw_path = getattr(vector_store, "path")
        store_path = Path(raw_path)
    else:
        store_path = Path(getattr(settings, "chroma_dir", "store/chroma"))
    return str(store_path), store_path.exists()


if FRONTEND_DIST.exists():
    app.mount("/", SpaStaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
