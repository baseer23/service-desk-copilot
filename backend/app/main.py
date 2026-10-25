"""Case API, ingestion endpoints and a lifespan-owned MCP/SQLite graph runtime."""

from __future__ import annotations

import asyncio
import base64
import secrets
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import SecretStr
from pydantic import BaseModel, Field
from starlette.responses import Response

from backend.app.agent.checkpoints import checkpoint_store
from backend.app.agent.graph import ServiceDeskAgent
from backend.app.core.config import Settings, get_settings
from backend.app.services.knowledge_policy import is_test_knowledge
from backend.app.diagnostics.readiness import ReadinessReport
from backend.app.mcp.client import DeskMateMCPClient, MCPConnectionError, MCPToolError
from backend.app.models.dto import (
    AskResponse,
    CaseCreateRequest,
    CaseEvent,
    CaseResponse,
    CaseResumeRequest,
    HealthResponse,
    EmailTurnRequest,
    IngestPasteRequest,
    IngestPasteResponse,
    IngestPdfResponse,
    IngestUrlRequest,
    IngestUrlResponse,
    MemorySaveRequest,
)
from backend.app.models.provider import LLMError
from backend.app.models.provider_factory import get_selectable_provider as get_provider
from backend.app.models.provider_routing import provider_scope, selected_provider
from backend.app.mcp.multi import MultiMCPClient
from backend.app.services.web import FetchError, UnsafeURL
from backend.app.store.vector_chroma import StorageError


class WorkspaceResetConfirm(BaseModel):
    confirmation: str = Field(min_length=6, max_length=20)
    confirmation_token: str = Field(min_length=20, max_length=200)


def llm_error_detail(error: LLMError) -> dict[str, Any]:
    """Return an actionable, secret-free provider diagnostic for the UI."""
    if error.category == "application_validation":
        return {
            "code": "APPLICATION_VALIDATION_FAILED",
            "message": "DeskMate rejected an invalid investigation step. Collected evidence was preserved.",
        }
    messages = {
        "authentication": "Azure OpenAI authentication failed. Sign in to Entra again and retry.",
        "authorization": (
            "Azure OpenAI denied this identity. Ask an administrator to verify the resource role assignment."
        ),
        "configuration": (
            "Azure OpenAI could not find the configured resource or deployment. "
            "Verify the endpoint, deployment, and API version."
        ),
        "capacity": "Azure OpenAI is rate limited or out of quota. Retry after the service reset.",
        "timeout": "Azure OpenAI timed out. The request was not automatically retried.",
        "context_length": "The Azure OpenAI request was too large after bounded compaction.",
        "structured_output": "Azure OpenAI returned an invalid structured response. Retry the investigation.",
    }
    return {
        "code": "AZURE_OPENAI_" + error.category.upper(),
        "message": messages.get(
            error.category,
            "Azure OpenAI could not complete the request. Ask an operator to inspect provider diagnostics.",
        ),
        **({"request_id": error.request_id} if error.request_id else {}),
    }


def case_response(state):
    data = {**state, "thread_id": state["case_id"]}
    resolution = state.get("voice_intake", {}).get("resolution_status", "unconfirmed")
    if resolution in {"unconfirmed", "user_confirmed_resolved", "escalation_required"}:
        data.setdefault("resolution_status", resolution)
    # Only explicit provenance/labels identify sample history. Existing records
    # remain intact, including rows whose origin is unknown.
    if data.get("origin") not in {"user", "demo", "test", "unknown"}:
        data.pop("origin", None)
    if not data.get("origin"):
        summary = state.get("ticket", {}).get("summary", "").casefold()
        data["origin"] = (
            "test"
            if summary.startswith(("fictional test:", "acceptance test:"))
            else "demo"
            if summary.startswith(("fictional ", "demo:"))
            else "unknown"
        )
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
        if s.deployment_mode == "production":
            raise ValueError("Production requires a persistent session signing key")
        s = s.model_copy(update={"voice_session_signing_key": SecretStr(secrets.token_urlsafe(48))})

    @asynccontextmanager
    async def lifespan(app):
        mcp = MultiMCPClient(s, builtin=DeskMateMCPClient(s))
        app.state.settings, app.state.mcp = s, mcp
        provider = None
        ingestion_task = None
        try:
            try:
                provider = get_provider(s)
            except LLMError:
                # Readiness remains available; generation routes reject configuration
                # errors explicitly. No fake provider is constructed as a fallback.
                provider = None
            await mcp.start()
            from backend.app.services.local_documents import graph_writer_readiness

            app.state.graph_writer = await asyncio.to_thread(graph_writer_readiness, s)
            from backend.app.core.principal import Principal, principal_scope

            system_tenant = (
                s.auth_allowed_tenants[0] if s.auth_allowed_tenants else s.voice_tenant_id
            )
            with principal_scope(Principal(system_tenant, "system-runtime", True)):
                async with checkpoint_store(s) as saver:
                    app.state.agent = ServiceDeskAgent(s, mcp, provider, saver)
                    await app.state.agent.initialize()
                    from backend.app.voice.history import VoiceHistory
                    from backend.app.conversation.sessions import ConversationSessions
                    from backend.app.services.conversation_history import ConversationHistory

                    app.state.voice = VoiceHistory(s)
                    app.state.conversation_sessions = ConversationSessions()
                    app.state.conversation_history = ConversationHistory(s)
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
                    if s.uses_record_store:
                        from backend.app.services.document_worker import document_worker

                        ingestion_task = asyncio.create_task(
                            document_worker(s, app.state.agent.harness)
                        )
                        app.state.ingestion_task = ingestion_task
                    yield
        finally:
            active_ingestion = getattr(app.state, "ingestion_task", ingestion_task)
            for task in {ingestion_task, active_ingestion} - {None}:
                task.cancel()
            if ingestion_task or active_ingestion:
                await asyncio.gather(
                    *({ingestion_task, active_ingestion} - {None}), return_exceptions=True
                )
            await app.state.speech.close()
            await mcp.close()
            if provider and hasattr(provider, "aclose"):
                await provider.aclose()
            elif provider and hasattr(provider, "close"):
                await asyncio.wait_for(asyncio.to_thread(provider.close), 5)

    app = FastAPI(
        title="DeskMate Case API",
        lifespan=lifespan,
        docs_url=None if s.deployment_mode == "production" else "/docs",
        redoc_url=None,
        openapi_url=None if s.deployment_mode == "production" else "/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-DeskMate-Provider"],
    )
    from backend.app.speech.api import register_speech_routes

    register_speech_routes(app, s)

    @app.middleware("http")
    async def protect_voice_cases(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path.startswith(("/cases/voice-", "/requests/voice-")):
            from fastapi.responses import JSONResponse

            from backend.app.voice.auth import COOKIE_NAME, VoiceError, verify_actor

            try:
                from backend.app.voice.history import browser_guard

                browser_guard(request, s)
                if s.deployment_mode == "production":
                    from backend.app.core.principal import current_principal
                    from backend.app.voice.auth import Actor

                    principal = current_principal(s)
                    actor = Actor(principal.owner_id, principal.tenant_id)
                else:
                    actor = verify_actor(s, request.cookies.get(COOKIE_NAME))
                session_id = request.url.path.split("/")[2].removeprefix("voice-")
                await app.state.voice.store(app.state.voice.repository.get, session_id, actor)
                if s.uses_record_store and s.deployment_mode == "local":
                    from backend.app.core.principal import Principal, principal_scope

                    with principal_scope(Principal(actor.tenant_id, actor.owner_id)):
                        return await call_next(request)
            except VoiceError as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})
        return await call_next(request)

    from fastapi.responses import JSONResponse

    from backend.app.core.authentication import authenticate, mutation_origin, register_auth_routes
    from backend.app.core.principal import current_principal, principal_scope

    register_auth_routes(app, s)

    @app.middleware("http")
    async def tenant_authority(request: Request, call_next):
        public = request.url.path in {
            "/",
            "/health",
            "/auth/configuration",
            "/auth/session",
            "/auth/logout",
        } or request.url.path.startswith("/assets/")
        if request.method == "OPTIONS" or public:
            return await call_next(request)
        try:
            mutation_origin(request, s)
            actor = await authenticate(request, s)
            from backend.app.services.workspace_reset import workspace_blocked

            if (
                request.method in {"POST", "PUT", "PATCH", "DELETE"}
                and workspace_blocked(actor.tenant_id)
                and not request.url.path.startswith("/admin/workspace-reset/")
            ):
                raise HTTPException(423, "This workspace is being reset. Try again shortly.")
            selection = request.headers.get("X-DeskMate-Provider", s.generation_provider)
            if s.test_mode and s.generation_provider == "stub":
                selection = "stub"
            if selection not in {"groq", "azure_openai", "stub"} or (
                selection == "stub" and not s.test_mode
            ):
                raise HTTPException(422, "Choose Groq or Azure OpenAI")
            with principal_scope(actor), provider_scope(selection):
                return await call_next(request)
        except HTTPException as error:
            return JSONResponse(status_code=error.status_code, content={"detail": error.detail})

    @app.get("/admin/readiness")
    async def operator_readiness():
        if not current_principal(s).operator:
            raise HTTPException(403, "This action requires an administrator.")
        return (await app.state.voice.store(readiness)).model_dump()

    @app.post("/admin/workspace-reset/prepare")
    async def prepare_workspace_reset():
        actor = current_principal(s)
        if not actor.operator:
            raise HTTPException(403, "This action requires an administrator.")
        from backend.app.services.workspace_reset import WorkspaceReset

        return await asyncio.to_thread(WorkspaceReset(s).prepare, actor.tenant_id, actor.owner_id)

    @app.post("/admin/workspace-reset/confirm")
    async def confirm_workspace_reset(body: WorkspaceResetConfirm):
        actor = current_principal(s)
        if not actor.operator:
            raise HTTPException(403, "This action requires an administrator.")
        if body.confirmation != "DELETE":
            raise HTTPException(422, "Type DELETE exactly to confirm the workspace reset")
        from backend.app.agent.streaming import cancel_workspace_streams
        from backend.app.services.workspace_reset import WorkspaceReset

        await cancel_workspace_streams(actor.tenant_id)
        ingestion = getattr(app.state, "ingestion_task", None)
        if ingestion and not ingestion.done():
            ingestion.cancel()
            await asyncio.gather(ingestion, return_exceptions=True)
        try:
            result = await WorkspaceReset(s).confirm(
                actor.tenant_id, actor.owner_id, body.confirmation_token
            )
            if result["status"] == "complete":
                app.state.conversation_sessions.clear()
                await asyncio.to_thread(app.state.conversation_history.records.close)
                if s.uses_record_store:
                    from backend.app.services.document_worker import document_worker

                    app.state.ingestion_task = asyncio.create_task(
                        document_worker(s, app.state.agent.harness),
                        name="document-worker-after-reset",
                    )
            return result
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

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
        report = await app.state.voice.store(readiness)
        return {
            "runtime_mode": "isolated_demo"
            if report.runtime_mode == "isolated_demo"
            else "standard",
            "text_ready": report.text_ready,
            "voice_ready": report.voice_ready,
            "local_documents_ready": report.local_documents_ready,
            "local_ocr_available": report.local_ocr_available,
            "semantic_ingestion_ready": report.semantic_ingestion_ready,
            "url_ingestion_enabled": report.url_ingestion_enabled,
            "managed_documents": s.uses_record_store,
            "azure_document_intelligence_available": bool(
                s.uses_record_store and s.azure_document_endpoint
            ),
            "neo4j_read_available": bool(
                app.state.mcp.integration_status.get("neo4j") == "connected"
                and {"neo4j__get-schema", "neo4j__read-cypher"}.issubset(
                    app.state.mcp.discovered
                )
            ),
            "structured_ingestion_available": bool(
                s.uses_record_store and app.state.graph_writer["available"]
            ),
            "hybrid_policy_enabled": True,
            "automatic_graph_ingestion_enabled": s.auto_document_graph_ingestion,
            "structured_ingestion_blocker": app.state.graph_writer["blocker"],
        }

    @app.get("/health")
    async def health():
        if s.deployment_mode == "production":
            return {
                "status": "ok"
                if app.state.local_storage["chroma"] and app.state.local_storage["sqlite"]
                else "degraded"
            }
        report = await app.state.voice.store(readiness)
        environment = app.state.local_storage
        return HealthResponse(
            status="ok" if report.text_ready else "degraded",
            generation_provider=selected_provider(s),
            test_mode=s.test_mode,
            mcp_connected=report.local_mcp == "READY",
            mcp_integrations=dict(app.state.mcp.integration_status),
            chroma=environment["chroma"],
            aura_configured=bool(s.neo4j_uri),
            aura_connected=environment["aura_connected"],
            speech_enabled=s.speech_enabled,
            speech_configured=report.voice.configured,
            embedding_provider=s.embed_provider,
            azure_openai_configured=(
                selected_provider(s) == "azure_openai" and report.reasoning.configured
            ),
            readiness=report.model_dump(),
        )

    async def require_reasoning() -> None:
        report = await app.state.voice.store(readiness)
        if not report.text_ready:
            raise HTTPException(
                503,
                {
                    "code": "REASONING_NOT_READY",
                    "message": "The selected AI service is unavailable. Check its configuration or choose another provider. Your saved work is preserved.",
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
        uploaded = await document_upload(file, title, "save_staged_document")
        processed = (
            await app.state.agent.harness.invoke(
                "index_document", {"document_id": uploaded["id"]}
            )
        )["result"]
        pages = processed.get("pages", uploaded.get("pages", 1))
        return {
            "pages": pages, "chunks": processed["chunks"],
            "entities": 0, "vector_count": processed["chunks"],
            "ms": 0, "duplicate": uploaded["duplicate"], "content_hash": uploaded["id"],
        }

    async def require_auto_graph_writer(*, requires_azure: bool) -> None:
        if not s.auto_document_graph_ingestion:
            return
        if not s.uses_record_store or (requires_azure and not s.azure_document_endpoint):
            raise HTTPException(
                503,
                "Automatic ingestion requires a persistent local document store"
                + (" and Azure DI endpoint." if requires_azure else "."),
            )
        # Revalidate the writer BEFORE starting a potentially billable extraction.
        from backend.app.services.local_documents import graph_writer_readiness

        writer = await asyncio.to_thread(graph_writer_readiness, s)
        app.state.graph_writer = writer
        if not writer["available"]:
            raise HTTPException(
                503,
                "Authorized Neo4j ingestion writer is unavailable. No Azure extraction was started.",
            )

    async def document_upload(
        file: UploadFile, title: str | None, tool: str,
        ingestion_mode: str = "structured_graph",
        document_processing: str = "local",
    ):
        from pathlib import Path

        suffix = Path(file.filename or "").suffix.lower()
        media_type = {
            ".pdf": "application/pdf",
            ".txt": "text/plain",
            ".md": "text/markdown",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".tiff": "image/tiff",
        }.get(suffix)
        if media_type is None:
            raise HTTPException(400, "Choose a PDF, Word, image, .txt or .md document")
        local_text = media_type in {"text/plain", "text/markdown"}
        await require_auto_graph_writer(requires_azure=not local_text)
        staging = (s.local_documents_dir / ".uploads").resolve()
        staging.mkdir(mode=0o700, parents=True, exist_ok=True)
        token = secrets.token_hex(32)
        staged = staging / token
        try:
            with staged.open("xb") as target:
                while chunk := await file.read(1024 * 1024):
                    target.write(chunk)
                target.flush()
            arguments = {"upload_token": token, "media_type": media_type}
            if tool in {"save_document", "save_staged_document"}:
                tool = "save_staged_document"
                arguments["title"] = title or file.filename
                arguments["ingestion_mode"] = ingestion_mode
                arguments["document_processing"] = (
                    "local" if s.auto_document_graph_ingestion and local_text
                    else document_processing
                )
            elif tool == "extract_staged_document" and s.auto_document_graph_ingestion:
                arguments["title"] = title or file.filename
                arguments["graph_enrich"] = True
            return (await app.state.agent.harness.invoke(tool, arguments))["result"]
        finally:
            staged.unlink(missing_ok=True)

    @app.post("/documents/extract")
    async def extract_upload(file: UploadFile = File(...)):
        return await document_upload(file, file.filename, "extract_staged_document")

    @app.post("/documents/upload")
    async def save_upload(
        file: UploadFile = File(...), title: str | None = Form(None),
        ingestion_mode: str = Form("structured_graph"),
        document_processing: str = Form("local"),
    ):
        if ingestion_mode != "structured_graph":
            raise HTTPException(400, "Hybrid ingestion is required and cannot be disabled")
        if document_processing not in {"local", "azure_di"}:
            raise HTTPException(400, "document_processing must be local or azure_di")
        if s.auto_document_graph_ingestion:
            ingestion_mode, document_processing = "structured_graph", "azure_di"
        return await document_upload(
            file, title, "save_document", ingestion_mode, document_processing
        )

    @app.post("/documents/paste")
    async def save_pasted_document(body: IngestPasteRequest):
        await require_auto_graph_writer(requires_azure=False)
        return (
            await app.state.agent.harness.invoke(
                "save_document",
                {
                    "data_base64": base64.b64encode(body.text.encode()).decode(),
                    "title": body.title,
                    "media_type": "text/plain",
                    "ingestion_mode": "structured_graph",
                    "document_processing": "local",
                },
            )
        )["result"]

    @app.get("/documents")
    async def list_local_documents():
        return (await app.state.agent.harness.invoke("list_documents", {}))["items"]

    @app.post("/documents/{document_id}/index")
    async def index_local_document(document_id: str):
        report = await app.state.voice.store(readiness)
        if not report.semantic_ingestion_ready:
            raise HTTPException(
                503,
                {
                    "code": "LOCAL_EMBEDDINGS_NOT_READY",
                    "message": report.semantic_ingestion_blocker,
                },
            )
        return (
            await app.state.agent.harness.invoke("index_document", {"document_id": document_id})
        )["result"]

    @app.post("/documents/{document_id}/reprocess")
    async def reprocess_local_document(document_id: str):
        return (
            await app.state.agent.harness.invoke(
                "reprocess_document",
                {
                    "document_id": document_id,
                    "document_processing": "azure_di",
                    "operator_reviewed_inflight": True,
                },
            )
        )["result"]

    @app.post("/documents/{document_id}/enrich-graph")
    async def enrich_local_document_graph(document_id: str):
        return (
            await app.state.agent.harness.invoke(
                "enrich_document_graph", {"document_id": document_id}
            )
        )["result"]

    @app.get("/documents/{document_id}/status")
    async def document_status(document_id: str):
        return (await app.state.agent.harness.invoke("get_document", {"document_id": document_id}))[
            "result"
        ]

    @app.get("/documents/{document_id}/download")
    async def document_download(document_id: str):
        value = (
            await app.state.agent.harness.invoke(
                "get_document_original", {"document_id": document_id}
            )
        )["result"]
        return Response(
            base64.b64decode(value["data_base64"]),
            media_type=value["media_type"],
            headers={
                "Content-Disposition": "attachment",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
            },
        )

    @app.post("/email-correction/start")
    async def start_email_correction():
        try:
            await app.state.agent.harness.invoke("list_registry", {"registry_type": "domains"})
        except (MCPToolError, MCPConnectionError) as exc:
            raise HTTPException(
                503, "Email lookup is temporarily unavailable. Retry shortly."
            ) from exc
        session = app.state.conversation_sessions.create()
        session.active_entity = "contact.email"
        return {
            "session_id": session.session_id,
            "response": "Hello, welcome to Digital nGenuity support. Could I have your email address, please?",
            "email": None,
            "confirmed": False,
            "provider": selected_provider(s),
            "version": 0,
        }

    @app.post("/voice-conversation/start")
    async def start_voice_conversation():
        session = app.state.conversation_sessions.create()
        return {
            "session_id": session.session_id,
            "response": "Hello, welcome to Digital nGenuity support. How can I help you today?",
            "mode": "helpdesk",
        }

    @app.post("/voice-conversation/{session_id}")
    async def voice_conversation(session_id: str, body: EmailTurnRequest):
        from backend.app.conversation.engine import ConversationEngine

        session = app.state.conversation_sessions.get(session_id)
        if not session:
            raise HTTPException(404, "Voice conversation expired. Start a new conversation.")
        return await ConversationEngine(
            app.state.agent.harness, app.state.agent.provider, selected_provider(s)
        ).process(session, body.summary, body.request_id, body.expected_version)

    @app.post("/email-correction/{session_id}")
    async def correct_email(session_id: str, body: EmailTurnRequest):
        from backend.app.conversation.engine import ConversationEngine

        session = app.state.conversation_sessions.get(session_id)
        if not session:
            raise HTTPException(404, "Email correction session expired. Start a new conversation.")
        session.active_entity = "contact.email"
        return await ConversationEngine(
            app.state.agent.harness, app.state.agent.provider, selected_provider(s)
        ).process(session, body.summary, body.request_id, body.expected_version)

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
        outcome = await app.state.agent.harness.invoke(
            "ingest_url", body.model_dump(exclude_none=True)
        )
        if outcome.get("status") != "ok" or not isinstance(outcome.get("result"), dict):
            raise HTTPException(502, outcome.get("error") or "Website ingestion failed")
        return outcome["result"]

    async def load(case_id):
        state = (await app.state.agent.harness.invoke("get_case", {"case_id": case_id}))["result"]
        if state is None:
            raise HTTPException(404, "Case not found")
        return state

    async def request_result(request_id, summary=None, answer=None, message=None):
        await require_reasoning()
        try:
            assertion = summary if summary is not None else message if message is not None else answer
            if assertion and s.uses_record_store:
                await app.state.agent.harness.invoke(
                    "capture_semantic_memory",
                    {"text": assertion, "conversation_id": request_id},
                )
            state, events = await app.state.agent.run_request(request_id, summary, answer, message)
            await asyncio.to_thread(app.state.conversation_history.save_turn, request_id, state)
            return AskResponse(
                request_id=request_id,
                intent="knowledge_question" if state["workflow"] == "knowledge" else "service_desk",
                case_id=request_id if state.get("persist_case") else None,
                result=state,
                events=events,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError:
            raise HTTPException(404, "Conversation not found") from None
        except LLMError as exc:
            from fastapi.responses import JSONResponse

            stored = (await app.state.agent.harness.invoke("get_case", {"case_id": request_id}))[
                "result"
            ]
            return JSONResponse(
                status_code=502,
                content={
                    "detail": llm_error_detail(exc)
                    if selected_provider(s) == "azure_openai"
                    else (
                        "The investigation could not finish. Your saved work is preserved."
                        if s.deployment_mode == "production"
                        else str(exc)
                    ),
                    "case_id": request_id if stored else None,
                },
            )
        except (MCPToolError, MCPConnectionError, StorageError) as exc:
            from fastapi.responses import JSONResponse

            stored = (await app.state.agent.harness.invoke("get_case", {"case_id": request_id}))[
                "result"
            ]
            return JSONResponse(
                status_code=502,
                content={
                    "detail": "The investigation could not finish. Your saved work is preserved."
                    if s.deployment_mode == "production"
                    else str(exc),
                    "case_id": request_id if stored else None,
                },
            )

    @app.post("/ask", response_model=AskResponse)
    async def ask(request: Request, body: CaseCreateRequest):
        if "text/event-stream" in request.headers.get("accept", ""):
            return stream_operation(request, lambda: request_result(uuid.uuid4().hex, body.summary))
        return await request_result(uuid.uuid4().hex, body.summary)

    @app.get("/memory/profile")
    async def memory_profile():
        return (await app.state.agent.harness.invoke("get_authenticated_profile", {}))["result"]

    @app.delete("/memory/profile")
    async def forget_memory_profile():
        return (await app.state.agent.harness.invoke("forget_authenticated_profile", {}))["result"]

    @app.post("/memory/save")
    async def save_memory(body: MemorySaveRequest):
        if not s.local_persistence_enabled:
            raise HTTPException(503, "Durable personal memory is disabled")
        result = (await app.state.agent.harness.invoke(
            "capture_semantic_memory",
            {"text": body.text, "conversation_id": body.conversation_id or uuid.uuid4().hex},
        ))["result"]
        if not result.get("captured") and result.get("reason") != "duplicate":
            raise HTTPException(422, "An explicit remember instruction is required")
        return result

    @app.get("/memory")
    async def list_memory(query: str | None = None, limit: int = 10):
        if query:
            return (await app.state.agent.harness.invoke(
                "search_semantic_memory", {"query": query, "limit": limit}
            ))["result"]
        return (await app.state.agent.harness.invoke("get_authenticated_profile", {}))["result"]

    @app.delete("/memory/{fact_id}")
    async def forget_memory(fact_id: str):
        result = (await app.state.agent.harness.invoke(
            "forget_semantic_memory", {"fact_id": fact_id}
        ))["result"]
        if not result.get("revoked"):
            raise HTTPException(404, "Memory fact not found")
        return result

    @app.get("/conversations")
    async def list_conversations():
        return await asyncio.to_thread(app.state.conversation_history.list)

    @app.get("/conversations/{conversation_id}", response_model=AskResponse)
    async def get_conversation(conversation_id: str):
        record = await asyncio.to_thread(app.state.conversation_history.get, conversation_id)
        if record is None:
            raise HTTPException(404, "Conversation not found")
        snapshot = await app.state.agent.graph.aget_state(app.state.agent.config(conversation_id))
        if not snapshot.values:
            raise HTTPException(409, "Conversation checkpoint is unavailable; it was not replaced")
        state = dict(snapshot.values)
        from backend.app.core.principal import current_principal

        if state.get("request_owner") and state["request_owner"] != current_principal(s).owner_id:
            raise HTTPException(404, "Conversation not found")
        events = []
        if record.get("linked_case_id"):
            events = (await app.state.agent.harness.invoke(
                "get_case_history", {"case_id": conversation_id}
            ))["items"]
        return AskResponse(
            request_id=conversation_id,
            intent="knowledge_question" if state.get("workflow") == "knowledge" else "service_desk",
            case_id=record.get("linked_case_id"),
            result=state,
            events=events,
            activity=record.get("activity", []),
        )

    @app.post("/requests/{request_id}/resume", response_model=AskResponse)
    async def resume_request(request: Request, request_id: str, body: CaseResumeRequest):
        if "text/event-stream" in request.headers.get("accept", ""):
            return stream_operation(request, lambda: request_result(request_id, answer=body.answer))
        return await request_result(request_id, answer=body.answer)

    @app.post("/requests/{request_id}/message", response_model=AskResponse)
    async def conversation_message(request: Request, request_id: str, body: CaseCreateRequest):
        if "text/event-stream" in request.headers.get("accept", ""):
            return stream_operation(
                request, lambda: request_result(request_id, message=body.summary)
            )
        return await request_result(request_id, message=body.summary)

    def stream_operation(request: Request, operation):
        from backend.app.agent.streaming import investigation_stream
        actor = current_principal(s)

        async def persist_activity(result, activity):
            request_id = getattr(result, "request_id", None)
            if request_id:
                await asyncio.to_thread(
                    app.state.conversation_history.save_activity,
                    request_id,
                    activity,
                    principal=actor,
                )

        return investigation_stream(request, s, operation, persist_activity)

    @app.post("/ask/stream")
    async def ask_stream(request: Request, body: CaseCreateRequest):
        return stream_operation(request, lambda: request_result(uuid.uuid4().hex, body.summary))

    @app.post("/requests/{request_id}/resume/stream")
    async def resume_stream(request: Request, request_id: str, body: CaseResumeRequest):
        return stream_operation(request, lambda: request_result(request_id, answer=body.answer))

    @app.post("/requests/{request_id}/message/stream")
    async def message_stream(request: Request, request_id: str, body: CaseCreateRequest):
        return stream_operation(request, lambda: request_result(request_id, message=body.summary))

    @app.get("/cases", response_model=list[CaseResponse])
    async def list_cases(request: Request) -> list[CaseResponse]:
        from backend.app.voice.auth import COOKIE_NAME, VoiceError, verify_actor

        async def visible(state: dict[str, Any]) -> bool:
            if not state["case_id"].startswith("voice-"):
                return True
            try:
                if s.deployment_mode == "production":
                    from backend.app.voice.auth import Actor

                    principal = current_principal(s)
                    actor = Actor(principal.owner_id, principal.tenant_id)
                else:
                    actor = verify_actor(s, request.cookies.get(COOKIE_NAME))
                await app.state.voice.store(
                    app.state.voice.repository.get,
                    state["case_id"].removeprefix("voice-"),
                    actor,
                )
                return True
            except VoiceError:
                return False

        if s.uses_record_store and s.deployment_mode == "local":
            from backend.app.core.principal import Principal, principal_scope

            try:
                actor = verify_actor(s, request.cookies.get(COOKIE_NAME))
            except VoiceError:
                items = (await app.state.agent.harness.invoke("list_cases", {}))["items"]
            else:
                with principal_scope(Principal(actor.tenant_id, actor.owner_id)):
                    items = (await app.state.agent.harness.invoke("list_cases", {}))["items"]
        else:
            items = (await app.state.agent.harness.invoke("list_cases", {}))["items"]
        return [
            case_response(state)
            for state in items
            if await visible(state)
            and (
                s.test_mode
                or not (
                    state.get("origin") in {"demo", "test"}
                    or is_test_knowledge(
                        {
                            "title": state.get("ticket", {}).get("summary", ""),
                            "source_system": state.get("source_system", ""),
                        }
                    )
                )
            )
        ]

    @app.post("/cases", response_model=CaseResponse, status_code=201)
    async def create_case(body: CaseCreateRequest):
        case_id = uuid.uuid4().hex
        state = {
            "case_id": case_id,
            "ticket": {"summary": body.summary},
            "status": "new",
            "confidence": 0,
            "origin": "test" if s.test_mode else "user",
        }
        await app.state.agent.harness.invoke("save_case", {"case_id": case_id, "state": state})
        await app.state.agent.note(state, "create_case", "Case created", "human_input")
        return case_response(state)

    @app.get("/cases/{case_id}", response_model=CaseResponse)
    async def get_case(case_id: str):
        return case_response(await load(case_id))

    async def run(case_id, answer=None):
        await require_reasoning()
        await load(case_id)
        try:
            return case_response(await app.state.agent.run_case(case_id, answer))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except LLMError as exc:
            raise HTTPException(
                502,
                llm_error_detail(exc)
                if selected_provider(s) == "azure_openai"
                else (
                    "The investigation could not finish. Please try again later."
                    if s.deployment_mode == "production"
                    else str(exc)
                ),
            ) from exc
        except (MCPToolError, MCPConnectionError, StorageError) as exc:
            raise HTTPException(
                502,
                "The investigation could not finish. Please try again later."
                if s.deployment_mode == "production"
                else str(exc),
            ) from exc

    @app.post("/cases/{case_id}/investigate", response_model=CaseResponse)
    async def investigate(request: Request, case_id: str):
        if "text/event-stream" in request.headers.get("accept", ""):
            return stream_operation(request, lambda: run(case_id))
        return await run(case_id)

    @app.post("/cases/{case_id}/resume", response_model=CaseResponse)
    async def resume(request: Request, case_id: str, body: CaseResumeRequest):
        if "text/event-stream" in request.headers.get("accept", ""):
            return stream_operation(request, lambda: run(case_id, body.answer))
        return await run(case_id, body.answer)

    @app.post("/cases/{case_id}/investigate/stream")
    async def investigate_stream(request: Request, case_id: str):
        return stream_operation(request, lambda: run(case_id))

    @app.post("/cases/{case_id}/resume/stream")
    async def case_resume_stream(request: Request, case_id: str, body: CaseResumeRequest):
        return stream_operation(request, lambda: run(case_id, body.answer))

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

        return JSONResponse(
            status_code=getattr(exc, "status_code", 502),
            content={
                "detail": "The operation could not finish. Your saved work is preserved."
                if s.deployment_mode == "production"
                else str(exc)
            },
        )

    @app.exception_handler(UnsafeURL)
    @app.exception_handler(FetchError)
    async def web_error(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=400, content={"detail": str(exc)})

    from pathlib import Path

    from fastapi.staticfiles import StaticFiles
    from starlette.responses import FileResponse

    web_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (web_dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="assets")

        @app.get("/")
        @app.get("/admin")
        async def web_application():
            return FileResponse(
                web_dist / "index.html",
                headers={
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "same-origin",
                },
            )

    return app


app = create_app()
