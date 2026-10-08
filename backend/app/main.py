import logging
import logging.config
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Tuple

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
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
    IngestUrlRequest,
    IngestUrlResponse,
    ProviderToggleRequest,
)
from backend.app.models.provider_factory import (
    ProviderContext,
    build_provider_context,
)
from backend.app.rag.answer import Responder
from backend.app.rag.planner import Planner
from backend.app.rag.retrieve import Retriever
from backend.app.services.ingest_service import IngestService
from backend.app.services.url_crawler import CrawlError, CrawlLimits, crawl_url
from backend.app.store.graph_repo import GraphRepository, InMemoryGraphRepository
from backend.app.store.vector_chroma import InMemoryVectorStore, VectorChromaStore
from backend.app.mcp.client import DeskMateMCPClient
from backend.app.workflows.research_graph import run_research_loop
from backend.app.agent.graph import ServiceDeskAgent
from backend.app.agent.memory import CaseMemoryStore

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
app.state.graph_backend = "inmemory"
app.state.provider = None  # populated via _set_active_provider
app.state.provider_context = None
app.state.active_provider_key = None
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
    aura_uri_prefixes = ("neo4j+s://", "neo4j+ssc://")
    uri = getattr(settings, "neo4j_uri", "") or ""
    is_aura = isinstance(uri, str) and uri.startswith(aura_uri_prefixes)

    if not is_aura:
        logger.info("Neo4j Aura URI not configured; vector-only mode is active")
        app.state.graph_backend = "vector-only"
        return InMemoryGraphRepository(), None

    if GraphDatabase is None:  # pragma: no cover - driver not installed
        logger.warning("neo4j driver not installed; vector-only mode is active")
        app.state.graph_backend = "vector-only"
        return InMemoryGraphRepository(), None

    driver = None
    try:
        driver = GraphDatabase.driver(uri, auth=(settings.neo4j_user, settings.neo4j_password))
        driver.verify_connectivity()
        repo = GraphRepository(driver)
        repo.ensure_constraints()
        app.state.graph_backend = "aura"
        return repo, driver
    except Exception as exc:  # pragma: no cover - handle offline graph
        logger.warning("Neo4j Aura unavailable (%s); vector-only mode is active", exc)
        if driver is not None:
            try:
                driver.close()
            except Exception:  # pragma: no cover - defensive cleanup
                pass
        app.state.graph_backend = "vector-only"
        return InMemoryGraphRepository(), None


def _initial_provider_key(settings) -> str:
    configured = (getattr(settings, "model_provider", "auto") or "auto").lower()
    if configured in {"auto", "local"}:
        return "groq" if getattr(settings, "groq_api_key", None) else "stub"
    if configured in {"hosted", "groq", "hosted-groq"}:
        return "groq"
    if configured in {"ollama", "llamacpp"}:
        return configured
    if configured == "stub":
        return "stub"
    return "stub"


def _set_active_provider(provider_key: str, *, settings=None) -> ProviderContext:
    settings = settings or getattr(app.state, "settings", SETTINGS)
    normalized = (provider_key or "stub").lower()
    context = build_provider_context(settings, normalized)
    app.state.provider = context.provider
    app.state.provider_context = context
    app.state.active_provider_key = normalized
    return context


def _resolve_provider_context(override: Optional[str], settings) -> ProviderContext:
    if override:
        return build_provider_context(settings, override)
    existing = getattr(app.state, "provider_context", None)
    if isinstance(existing, ProviderContext):
        return existing
    active_key = getattr(app.state, "active_provider_key", None) or _initial_provider_key(settings)
    return _set_active_provider(active_key, settings=settings)


def _require_admin_secret(request: Request, settings) -> None:
    expected = getattr(settings, "admin_api_secret", None)
    if not expected:
        return
    provided = request.headers.get("x-admin-secret")
    if provided != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


_set_active_provider(_initial_provider_key(SETTINGS), settings=SETTINGS)


def _seed_default_knowledge_if_needed(settings) -> None:
    """Populate a minimal default knowledge base when the live runtime is empty."""
    try:
        vector_store = getattr(app.state, "vector_store", None)
        if vector_store is None:
            return
        if hasattr(vector_store, "count") and vector_store.count() > 0:
            return

        service = IngestService(
            settings=settings,
            vector_store=vector_store,
            graph_repo=app.state.graph_repo,
            embedding_provider=_safe_embedding_provider(settings),
        )
        default_docs = [
            (
                "MFA reset guidance",
                "Multi-Factor Authentication (MFA) adds a second verification step such as a code from an authenticator app or SMS. Users should register a new device, confirm approval prompts, and contact the help desk if the verification method is unavailable.",
            ),
            (
                "Password reset procedure",
                "To reset a password, verify identity with the self-service portal, choose a new password that meets policy, and sign out of all active sessions if the account is suspected to be compromised.",
            ),
            (
                "Laptop access troubleshooting",
                "If a laptop cannot join the corporate network, confirm Wi-Fi access, ensure the VPN client is updated, and verify the device is enrolled in the management system before escalating to the service desk.",
            ),
        ]
        for title, text in default_docs:
            service.ingest_text(title, text)
        logger.info("Seeded default DeskMate knowledge into the runtime vector store")
    except Exception as exc:  # pragma: no cover - startup best effort only
        logger.warning("Default knowledge seed skipped: %s", exc)


@app.on_event("startup")
async def startup_event() -> None:  # pragma: no cover - exercised in integration tests
    """Initialise application state before serving requests."""
    settings = get_settings()
    app.state.settings = settings
    app.state.vector_store = _init_vector_store(settings)
    repo, driver = _init_graph_repo(settings)
    app.state.graph_repo = repo
    app.state.graph_driver = driver
    _set_active_provider(getattr(app.state, "active_provider_key", None) or _initial_provider_key(settings), settings=settings)
    _seed_default_knowledge_if_needed(settings)
    # Initialise case memory and service-desk agent
    try:
        app.state.case_store = CaseMemoryStore()
        app.state.service_agent = ServiceDeskAgent(mcp_client=DeskMateMCPClient(), memory_store=app.state.case_store)
        logger.info("ServiceDeskAgent and CaseMemoryStore initialised")
    except Exception as exc:  # pragma: no cover - best-effort only
        logger.warning("Failed to initialise ServiceDeskAgent or CaseMemoryStore: %s", exc)


@app.on_event("shutdown")
async def shutdown_event() -> None:  # pragma: no cover - exercised in integration tests
    """Tear down external resources on application shutdown."""
    driver = getattr(app.state, "graph_driver", None)
    if driver:
        driver.close()


class SpaStaticFiles(StaticFiles):
    """Serve SPA assets while falling back to index.html for unknown routes."""

    async def get_response(self, path: str, scope: dict[str, Any]) -> Response:  # type: ignore[override]
        """Serve static files and fall back to index.html for SPA routes."""
        try:
            return await super().get_response(path, scope)
        except Exception:  # pragma: no cover - relies on filesystem state
            index_file = Path(self.directory) / "index.html"
            if index_file.exists():
                return FileResponse(index_file)
            return JSONResponse({"detail": "Not Found"}, status_code=404)


@app.middleware("http")
async def enforce_body_limit(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Reject payloads that exceed configured request body limits."""
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
async def log_requests(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Log request method/path pairs alongside the response status."""
    response = await call_next(request)
    logger.info("%s %s -> %s", request.method, request.url.path, response.status_code)
    return response


@app.get("/health")
def health() -> dict[str, object]:
    """Return deployment health, reachability, and provider metadata."""
    settings = getattr(app.state, "settings", SETTINGS)
    provider_context = _resolve_provider_context(None, settings)
    provider = provider_context.provider
    active_provider = getattr(app.state, "active_provider_key", _initial_provider_key(settings))
    vector_path, vector_exists = _vector_store_state(getattr(app.state, "vector_store", None), settings)
    hosted_reachable = _probe_hosted(settings)
    graph_backend = getattr(app.state, "graph_backend", "vector-only")

    preferred_local_models = ["phi3:mini", "llama3.2:3b", "qwen2.5:7b"] if provider_context.provider_type == "stub" else []
    return {
        "status": "ok",
        "provider": provider.name(),
        "provider_type": provider_context.provider_type,
        "model_name": provider_context.model_name,
        "provider_vendor": provider_context.vendor,
        "local_model_available": provider_context.local_model_available,
        "operator_message": provider_context.reason,
        "hosted_reachable": hosted_reachable,
        "hosted_model_name": getattr(settings, "hosted_model_name", None),
        "active_provider": active_provider,
        "active_model": provider_context.model_name,
        "graph_backend": graph_backend,
        "preferred_local_models": preferred_local_models,
        "ollama_reachable": _probe_ollama(settings),
        "llamacpp_reachable": _probe_llamacpp(settings),
        "neo4j_reachable": _probe_neo4j(getattr(app.state, "graph_repo", None), graph_backend),
        "vector_store_path": vector_path,
        "vector_store_path_exists": vector_exists,
    }


@app.post("/ingest/paste", response_model=IngestPasteResponse)
def ingest_paste(request: IngestPasteRequest) -> IngestPasteResponse:
    """Ingest pasted text content via the standard pipeline."""
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
async def ingest_pdf(file: UploadFile = File(...), title: Optional[str] = None) -> IngestPdfResponse:
    """Accept a PDF upload, extract text, and index it."""
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
    """Execute retrieval, provider selection, and response composition."""
    settings = getattr(app.state, "settings", SETTINGS)
    planner = Planner(settings=settings, graph_repo=app.state.graph_repo)
    plan = planner.plan(payload.question)
    top_k = payload.top_k or plan.get("top_k", settings.top_k)

    mode = plan.get("mode", "VECTOR").upper()
    provider_context = _resolve_provider_context(payload.provider_override, settings)

    logger.info("/ask received | Planner selected %s | LangGraph workflow start", mode)
    try:
        response = run_research_loop(
            settings=settings,
            vector_store=app.state.vector_store,
            graph_repo=app.state.graph_repo,
            embedding_provider=_safe_embedding_provider(settings),
            provider=provider_context.provider,
            question=payload.question,
            top_k=top_k,
        )
    except Exception as exc:  # pragma: no cover - workflow failure path
        logger.exception("LangGraph /ask workflow failed")
        raise HTTPException(status_code=502, detail=f"LangGraph workflow failed: {exc}") from exc

    return response


@app.post("/admin/provider")
def set_active_provider(selection: ProviderToggleRequest, request: Request) -> dict[str, object]:
    """Switch the active local provider, enforcing the admin secret."""
    settings = getattr(app.state, "settings", SETTINGS)
    _require_admin_secret(request, settings)
    context = _set_active_provider(selection.provider, settings=settings)
    return {
        "active_provider": app.state.active_provider_key,
        "model_name": context.model_name,
        "provider": context.provider.name(),
        "provider_type": context.provider_type,
        "reason": context.reason,
    }


@app.post("/ingest/url", response_model=IngestUrlResponse)
def ingest_url(request: IngestUrlRequest) -> IngestUrlResponse:
    """Ingest content discovered by crawling a single URL."""
    settings = get_settings()
    if not settings.allow_url_ingest:
        raise HTTPException(status_code=403, detail="URL ingestion is disabled")

    limits = CrawlLimits(
        max_depth=request.max_depth if request.max_depth is not None else settings.url_max_depth,
        max_pages=request.max_pages if request.max_pages is not None else settings.url_max_pages,
        max_total_chars=settings.url_max_total_chars,
        rate_limit_sec=settings.url_rate_limit_sec,
    )
    if limits.max_pages <= 0:
        raise HTTPException(status_code=400, detail="max_pages must be positive")

    started = time.perf_counter()
    try:
        crawl_result = crawl_url(request.url, settings=settings, overrides=limits)
    except CrawlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    service = IngestService(
        settings=settings,
        vector_store=app.state.vector_store,
        graph_repo=app.state.graph_repo,
        embedding_provider=_safe_embedding_provider(settings),
    )

    total_chunks = 0
    total_entities = 0
    total_vectors = 0
    pages_ingested = 0

    for page in crawl_result.pages:
        response = service.ingest_text(page.title, page.content)
        total_chunks += response.chunks
        total_entities += response.entities
        total_vectors += response.vector_count
        pages_ingested += 1

    latency_ms = int((time.perf_counter() - started) * 1000)

    return IngestUrlResponse(
        pages=pages_ingested,
        chunks=total_chunks,
        entities=total_entities,
        vector_count=total_vectors,
        ms=latency_ms,
    )


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


def _probe_neo4j(graph_repo, backend: str) -> bool:
    if graph_repo is None or backend != "aura":
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


@app.get("/cases")
def list_cases() -> dict[str, object]:
    store: CaseMemoryStore | None = getattr(app.state, "case_store", None)
    if store is None:
        return {"cases": []}
    return {"cases": store.list_cases()}


@app.get("/cases/{case_id}")
def get_case(case_id: str) -> dict[str, object]:
    store: CaseMemoryStore | None = getattr(app.state, "case_store", None)
    if store is None:
        raise HTTPException(status_code=404, detail="Case store unavailable")
    state = store.load_case(case_id) or {}
    return {"case_id": case_id, "state": state}


@app.post("/cases")
def create_or_update_case(payload: dict) -> dict[str, object]:
    store: CaseMemoryStore | None = getattr(app.state, "case_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Case store unavailable")
    case_id = payload.get("case_id") or payload.get("id")
    if not case_id:
        raise HTTPException(status_code=400, detail="Missing case_id")
    store.save_case(case_id, payload.get("state", payload))
    return {"case_id": case_id, "status": "saved"}


@app.post("/cases/{case_id}/investigate")
def investigate_case(case_id: str, payload: dict) -> dict[str, object]:
    agent: ServiceDeskAgent | None = getattr(app.state, "service_agent", None)
    if agent is None:
        raise HTTPException(status_code=503, detail="Service agent unavailable")
    ticket = payload.get("ticket") or {}
    question = payload.get("question")
    result = agent.run_case(case_id, ticket=ticket, question=question)
    return {"case_id": case_id, "result": result}


@app.post("/cases/{case_id}/resume")
def resume_case(case_id: str, payload: dict) -> dict[str, object]:
    """Resume a paused LangGraph investigation by recording a human answer and continuing the same case."""
    agent: ServiceDeskAgent | None = getattr(app.state, "service_agent", None)
    store: CaseMemoryStore | None = getattr(app.state, "case_store", None)
    if agent is None or store is None:
        raise HTTPException(status_code=503, detail="Agent or case store unavailable")
    answer = payload.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise HTTPException(status_code=400, detail="Missing answer")
    # Persist the human answer as a fact tied to the case so the agent can pick it up
    try:
        store.save_fact(case_id, source="human", fact_text=answer)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to persist resume answer: %s", exc)
    # Load existing case to get the ticket and continue
    state = store.load_case(case_id) or {}
    ticket = state.get("ticket") or {}
    # Continue the agent run which will load persisted state and incorporate remembered facts
    result = agent.run_case(case_id, ticket=ticket, question=None)
    return {"case_id": case_id, "result": result}


@app.post("/cases/{case_id}/resume")
def resume_case(case_id: str, payload: dict) -> dict[str, object]:
    """Resume an existing LangGraph investigation by applying a human answer and continuing the graph.

    Expected payload: { "answer": "2nd floor" }
    """
    store: CaseMemoryStore | None = getattr(app.state, "case_store", None)
    agent: ServiceDeskAgent | None = getattr(app.state, "service_agent", None)
    if store is None or agent is None:
        raise HTTPException(status_code=503, detail="Case store or agent unavailable")

    answer = payload.get("answer")
    if not answer:
        raise HTTPException(status_code=400, detail="Missing 'answer' in payload")

    # Persist the human answer as an observable fact and event so it's available to future cases
    fact = {
        "case_id": case_id,
        "subject": case_id,
        "predicate": "human_answer",
        "value": str(answer),
        "source": "human",
        "source_system": "ui",
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "confidence": 1.0,
        "reliability": "confirmed",
        "metadata": {},
    }
    try:
        store.save_fact(fact)
    except Exception:
        # best-effort; continue
        pass
    try:
        store.record_event(case_id, "human", "user", f"resume answer: {answer}")
    except Exception:
        pass

    # Load existing state to preserve ticket and other context
    existing_state = store.load_case(case_id) or {}
    ticket = existing_state.get("ticket") or {}

    # Re-run the agent for the same case; agent.run_case will load saved state and continue
    result = agent.run_case(case_id, ticket=ticket, question=None)
    return {"case_id": case_id, "result": result}


if FRONTEND_DIST.exists():
    app.mount("/", SpaStaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
