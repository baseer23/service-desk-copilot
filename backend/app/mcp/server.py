"""Typed MCP capabilities. Storage and external systems live only behind this boundary."""

import asyncio
import base64
import inspect
import logging
from datetime import datetime, timezone
from functools import lru_cache, wraps
from typing import Any, Literal

from backend.app.agent.memory import CaseMemoryStore
from backend.app.agent.models import CapabilityResult, MemoryFact
from backend.app.core.config import get_settings
from backend.app.repositories.fixtures import FixtureRepository
from mcp.server.fastmcp import Context, FastMCP

server = FastMCP("deskmate")
fixtures = FixtureRepository()


class URLIngestError(ValueError):
    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def capability(source_system, reliability=0.9):
    def decorate(fn):
        @wraps(fn)
        async def invoke(*args, ctx: Context = None, **kwargs):
            try:
                from backend.app.core.principal import (
                    current_principal,
                    principal_scope,
                    verify_principal,
                )

                settings = get_settings()
                system = source_system
                if (
                    (settings.deployment_mode == "production" or not settings.test_mode)
                    and fn.__name__ in {
                        "search_web", "search_news", "fetch_web_page", "ingest_url",
                        "get_weather", "geocode_location", "search_places", "get_route",
                        "convert_currency", "get_holidays",
                    }
                    and not settings.enable_public_web_tools
                ):
                    raise ValueError("Public research requires explicit workspace authorization")
                meta = ctx.request_context.meta if ctx is not None else None
                supplied = meta.model_dump().get("deskmate_authority") if meta else None
                if supplied:
                    actor = verify_principal(settings, supplied)
                else:
                    actor = current_principal(settings)
                with principal_scope(actor):
                    if source_system == "local_fixture" and not settings.enable_local_fixtures:
                        result = {
                            "available": False,
                            "reason": "This organizational data source is not connected.",
                        }
                    else:
                        result = (
                            await fn(*args, **kwargs)
                            if inspect.iscoroutinefunction(fn)
                            else await asyncio.to_thread(fn, *args, **kwargs)
                        )
                rows = result if isinstance(result, list) else [result] if result else []
                status = (
                    "unavailable"
                    if isinstance(result, dict) and result.get("available") is False
                    else "ok"
                )
                return CapabilityResult(
                    tool=fn.__name__,
                    source_system=system,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    reliability=reliability,
                    status=status,
                    items=rows,
                    result=result,
                )
            except Exception as exc:
                # No credentials or raw external response bodies in tool errors.
                message = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else f"{fn.__name__} failed ({type(exc).__name__})"
                )
                from backend.app.core.azure_errors import azure_error_code

                code = azure_error_code(exc)
                if code != "REQUEST_FAILED":
                    message += " [" + code + "]"
                return CapabilityResult(
                    tool=fn.__name__,
                    source_system=locals().get("system", source_system),
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    reliability=0,
                    status="error",
                    error=message,
                    result=(
                        {
                            **({"http_status": exc.status_code} if hasattr(exc, "status_code") else {}),
                            **({"code": exc.code} if hasattr(exc, "code") else {}),
                        }
                        if hasattr(exc, "status_code") or hasattr(exc, "code")
                        else None
                    ),
                )

        invoke.__annotations__ = {**fn.__annotations__, "ctx": Context, "return": CapabilityResult}
        import inspect

        signature = inspect.signature(fn)
        invoke.__signature__ = signature.replace(
            parameters=[
                *signature.parameters.values(),
                inspect.Parameter(
                    "ctx", inspect.Parameter.KEYWORD_ONLY, default=None, annotation=Context
                ),
            ],
            return_annotation=CapabilityResult,
        )
        from backend.app.agent.tools import READ_TOOLS

        available = source_system != "local_fixture" or get_settings().enable_local_fixtures
        server.tool(
            annotations={"readOnlyHint": fn.__name__ in READ_TOOLS},
            meta={"deskmate": {"source_system": source_system, "available": available}},
        )(invoke)
        return invoke

    return decorate


@lru_cache
def memory():
    if get_settings().uses_record_store:
        from backend.app.repositories.record_memory import RecordCaseMemoryStore

        return RecordCaseMemoryStore(managed_records())
    return CaseMemoryStore(get_settings().case_db_path)


@lru_cache
def managed_records():
    from backend.app.store.local_records import record_store

    return record_store(get_settings())


@lru_cache
def semantic_memory():
    from backend.app.services.semantic_memory import AuthorizedSemanticGraph, SemanticMemoryService

    settings = get_settings()
    return SemanticMemoryService(
        settings, records=managed_records(), graph=AuthorizedSemanticGraph(settings)
    )


@lru_cache
def environment():
    from backend.app.adapters.embeddings import get_embedding_provider
    from backend.app.rag.retrieve import Retriever
    from backend.app.services.capabilities import knowledge_stores
    from backend.app.services.ingest_service import IngestService

    s = get_settings()
    vector, graph, driver = knowledge_stores(s)
    if driver and s.test_mode and s.enable_local_fixtures:
        graph.upsert_relationships(fixtures.relationships())
    embedding = get_embedding_provider(s)
    return (
        vector,
        graph,
        driver,
        IngestService(s, vector, graph, embedding),
        Retriever(s, vector, graph, embedding),
    )


@lru_cache
def documents():
    if get_settings().local_persistence_enabled and get_settings().data_platform == "local":
        from backend.app.services.local_documents import LocalDocumentRepository

        return LocalDocumentRepository(get_settings(), records=managed_records())
    from backend.app.repositories.documents import DocumentRepository

    return DocumentRepository(get_settings().case_db_path)


@capability("local_registry")
def list_registry(registry_type: str):
    """List canonical values from a validated local registry."""
    from backend.app.resolution.registry import registry_store

    return {
        "registry_type": registry_type,
        "values": registry_store(get_settings().test_mode).list(registry_type),
    }


@capability("local_registry")
def resolve_entity(entity_type: str, candidate: str, current_value: str | None = None):
    """Resolve a candidate through its typed registry without model inference."""
    from backend.app.resolution.registry import registry_store

    return registry_store(get_settings().test_mode).resolve(entity_type, candidate, current_value)


@capability("local_registry")
def reload_registry(registry_type: str):
    """Safely reload a validated local registry."""
    from backend.app.resolution.registry import registry_store

    return {
        "registry_type": registry_type,
        "values": registry_store(get_settings().test_mode).reload(registry_type),
        "reloaded": True,
    }


@capability("chroma")
def search_knowledge(
    question: str,
    mode: Literal["VECTOR", "GRAPH", "HYBRID", "KEYWORD"] = "HYBRID",
    top_k: int = 6,
    graph_plan: dict | None = None,
):
    """Retrieve internal knowledge. VECTOR discovers semantically relevant local content even without a known title; HYBRID combines bounded graph references with local content fallback; GRAPH searches linked references; KEYWORD searches indexed text."""
    if mode.upper() not in {"VECTOR", "GRAPH", "HYBRID", "KEYWORD"}:
        raise ValueError("mode must be VECTOR, GRAPH, HYBRID or KEYWORD")
    if not 1 <= top_k <= 30:
        raise ValueError("top_k must be between 1 and 30")
    if mode.upper() == "KEYWORD":
        from backend.app.rag.retrieve import Retriever
        from backend.app.services.capabilities import knowledge_stores

        # Local extraction/search remains usable even without Azure credentials.
        vector, graph, _ = knowledge_stores(get_settings(), connect_graph=False)
        rows = Retriever(get_settings(), vector, graph, None, documents()).keyword_search(
            question, top_k
        )
    else:
        r = environment()[4]
        if mode.upper() == "HYBRID":
            rows = r.hybrid_search(question, top_k, graph_plan=graph_plan)
        else:
            rows = {"VECTOR": r.vector_search, "GRAPH": r.graph_search}[
                mode.upper()
            ](question, top_k)
    for row in rows:
        if row.get("metadata", {}).get("source_type") == "local_fixture":
            row["source_system"] = "local_fixture"
    return rows


@capability("neo4j_aura")
def search_graph(entities: list[str], limit: int = 12):
    """Discover bounded entity relationships and connected documents in optional Neo4j Aura; explicit unavailable status if unconfigured."""
    _, graph, driver, _, _ = environment()
    if not driver:
        return {
            "available": False,
            "reason": "Aura is not connected; semantic retrieval remains available.",
        }
    if not 1 <= limit <= 30:
        raise ValueError("limit must be between 1 and 30")
    return graph.search_relationships(entities, limit)


@capability("neo4j_aura")
def search_knowledge_entities(query: str, limit: int = 20):
    """Find tenant-scoped semantic entities and compact candidate metadata."""
    _, graph, driver, _, _ = environment()
    if not driver:
        return {"available": False, "reason": "Aura is not connected; local retrieval remains available."}
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    return graph.find_entity_candidates(query, limit=limit)


@capability("neo4j_aura")
def get_knowledge_entity_context(entity_id: str):
    """Return a bounded tenant-scoped semantic neighborhood for one known entity ID."""
    _, graph, driver, _, _ = environment()
    if not driver:
        return {"available": False, "reason": "Aura is not connected; local retrieval remains available."}
    return graph.get_entity_neighborhood(entity_id, max_hops=2, limit=30)


@capability("neo4j_aura")
def get_entity_document_references(entity_id: str):
    """Return linked document and logical Chroma object references for an entity."""
    _, graph, driver, _, _ = environment()
    if not driver:
        return {"available": False, "reason": "Aura is not connected; local retrieval remains available."}
    return graph.find_related_documents(entity_id, limit=20)


@capability("semantic_memory", 1.0)
def get_authenticated_profile():
    """Return confirmed profile facts for the authenticated tenant and owner only."""
    semantic_memory().retry_pending(limit=10)
    profile = semantic_memory().profile()
    return {"name": profile["name"], "facts": profile["facts"], "graph_status": profile["graph_status"]}


@capability("semantic_memory", 1.0)
def search_semantic_memory(query: str, limit: int = 10):
    """Search bounded confirmed personal memory; tenant and owner come from signed authority."""
    semantic_memory().retry_pending(limit=10)
    return semantic_memory().search(query, limit)


@capability("semantic_memory", 1.0)
def capture_semantic_memory(text: str, conversation_id: str, allow_personal_memory: bool = True):
    """Application-controlled capture of explicit first-person identity assertions."""
    return semantic_memory().capture_conversation(
        text, conversation_id, allow_personal_memory=allow_personal_memory
    )


@capability("semantic_memory", 1.0)
def forget_authenticated_profile():
    """Application-controlled revocation of the authenticated owner's personal memory."""
    return {"revoked": semantic_memory().forget_profile()}


@capability("semantic_memory", 1.0)
def forget_semantic_memory(fact_id: str):
    """Application-controlled revocation of one authenticated owner's fact."""
    return {"revoked": semantic_memory().forget(fact_id)}


@capability("local_fixture")
def search_previous_tickets(
    query: str, user: str | None = None, company: str | None = None, site: str | None = None
):
    """Find similar past incidents. Historical location/outcome does not establish the current user's location or root cause."""
    return fixtures.search_previous_tickets(query, user, company, site)


@capability("local_fixture")
def get_ticket(ticket_id: str):
    """Read one known fixture ticket by identifier."""
    return fixtures.get_ticket(ticket_id)


@capability("local_fixture")
def get_user_context(user_name: str, company: str | None = None):
    """User identity/company context. Location and installed client state may be unknown."""
    return fixtures.get_user_context(user_name, company)


@capability("local_fixture")
def get_company_context(company: str):
    """Company services and sites. Select only when company context affects the investigation."""
    return fixtures.get_company_context(company)


@capability("local_fixture")
def get_site_context(company: str, site: str):
    """Site infrastructure context when site-specific evidence makes location relevant."""
    return fixtures.get_site_context(company, site)


@capability("local_fixture")
def get_device_context(device_or_user: str):
    """Device/printer operational context, queues, ownership, update state and observed diagnostics."""
    return fixtures.get_device_context(device_or_user)


@capability("local_fixture")
def get_active_alerts(company: str, site: str | None = None, product: str | None = None):
    """Active service degradation alerts and technician acknowledgements. Useful for shared outages."""
    return fixtures.get_active_alerts(company, site, product)


@capability("local_fixture")
def get_recent_changes(company: str, site: str | None = None, product: str | None = None):
    """Recent service/device configuration changes; temporal correlation does not prove causation."""
    return fixtures.get_recent_changes(company, site, product)


@capability("public_web", 0.6)
def search_web(query: str, max_results: int = 3):
    """Public research for a specific evidence gap; prefer vendor/authoritative documentation. Search snippets are not verified facts."""
    from ddgs import DDGS

    if not 1 <= max_results <= 10:
        raise ValueError("max_results must be between 1 and 10")
    return [
        {
            "title": r.get("title", ""),
            "url": r["href"],
            "snippet": r.get("body", ""),
            "source_system": "public_web",
        }
        for r in DDGS(timeout=10).text(query, max_results=max_results)
        if r.get("href")
    ]


@capability("public_web", 0.6)
def search_news(
    query: str,
    max_results: int = 5,
    timelimit: Literal["d", "w", "m"] = "w",
):
    """Search current Canadian-English news for explicitly authorized research."""
    if not 1 <= max_results <= 10:
        raise ValueError("max_results must be between 1 and 10")
    if timelimit not in {"d", "w", "m"}:
        raise ValueError("timelimit must be d, w or m")
    from ddgs import DDGS
    from urllib.parse import urlsplit

    rows, seen = [], set()
    for item in DDGS(timeout=10).news(
        query, region="ca-en", timelimit=timelimit, max_results=max_results
    ):
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        parsed = urlsplit(url)
        if (
            not title or parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or url in seen
        ):
            continue
        published = item.get("date")
        if published:
            try:
                datetime.fromisoformat(str(published).replace("Z", "+00:00"))
            except ValueError:
                published = None
        seen.add(url)
        rows.append({
            "title": title,
            "url": url,
            "snippet": str(item.get("body") or "").strip(),
            "published_at": str(published) if published else None,
            "publisher": str(item.get("source") or "").strip() or None,
            "source_system": "public_web",
        })
    return rows


@capability("system_time", 1.0)
def get_current_time(timezone: str = "America/Halifax"):
    """Return the current date and timezone-aware time for an IANA timezone."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        current = datetime.now(ZoneInfo(timezone))
    except ZoneInfoNotFoundError:
        raise ValueError("Unknown IANA timezone name") from None
    return {
        "timezone": timezone,
        "timestamp": current.isoformat(),
        "date": current.date().isoformat(),
        "weekday": current.strftime("%A"),
    }


@capability("system_time", 1.0)
def calculate_date_time(
    operation: Literal["current", "convert", "add_days", "difference", "relative"],
    timezone_name: str = "America/Halifax",
    source_timezone: str | None = None,
    timestamp: str | None = None,
    other_timestamp: str | None = None,
    days: int = 0,
    relative: str | None = None,
):
    """Current time, timezone conversion, date arithmetic/difference, or relative date using IANA zones."""
    from backend.app.services.general_intelligence import date_time_operation

    return date_time_operation(
        operation, timezone_name, source_timezone, timestamp, other_timestamp, days, relative
    )


@capability("local_decimal", 1.0)
def calculate(expression: str):
    """Safely calculate arithmetic and percentages using exact decimal operations; no code execution."""
    from backend.app.services.general_intelligence import calculate as calculate_expression

    return calculate_expression(expression)


@capability("local_conversion", 1.0)
def convert_unit(value: float, from_unit: str, to_unit: str):
    """Convert supported distance, mass, volume and temperature units deterministically."""
    from backend.app.services.general_intelligence import convert_unit as convert

    return convert(value, from_unit, to_unit)


@capability("open_meteo", 0.9)
def geocode_location(query: str, count: int = 5):
    """Resolve a named location to coordinates and an IANA timezone before weather or routing."""
    from backend.app.services.general_intelligence import geocode_location as geocode

    return geocode(query, count)


@capability("open_meteo", 0.9)
def get_weather(location: str, forecast_days: int = 3, units: Literal["metric", "imperial"] = "metric"):
    """Get current observations and dated daily forecast from Open-Meteo; alerts are not claimed."""
    from backend.app.services.general_intelligence import get_weather as weather

    return weather(location, forecast_days, units)


@capability("openstreetmap", 0.8)
def search_places(query: str, near: str | None = None, count: int = 5):
    """Search OpenStreetMap places and nearby points of interest; ratings and hours are unavailable."""
    from backend.app.services.general_intelligence import search_places as places

    return places(query, near, count)


@capability("osrm", 0.8)
def get_route(origin: str, destination: str, mode: Literal["driving"] = "driving"):
    """Estimate route distance and duration without live traffic after geocoding both locations."""
    from backend.app.services.general_intelligence import get_route as route

    return route(origin, destination, mode)


@capability("frankfurter", 0.9)
def convert_currency(amount: float, base: str, quote: str, rate_date: str = "latest"):
    """Convert currencies using dated Frankfurter/ECB reference rates; not an executable market quote."""
    from backend.app.services.general_intelligence import convert_currency as currency

    return currency(amount, base, quote, rate_date)


@capability("nager_date", 0.85)
def get_holidays(country_code: str, year: int, subdivision: str | None = None):
    """List public holidays by ISO country and optional subdivision, preserving national/subdivision scope."""
    from backend.app.services.general_intelligence import get_holidays as holidays

    return holidays(country_code, year, subdivision)


@capability("public_web", 0.7)
def fetch_web_page(url: str):
    """Fetch/extract a bounded public HTML page with SSRF and redirect validation."""
    from backend.app.services.web import fetch_page

    return fetch_page(url, max_bytes=get_settings().web_max_bytes)


@capability("chroma")
def ingest_text(
    text: str,
    title: str | None = None,
    source: str = "paste",
    source_type: str = "paste",
    url: str = "",
):
    """Normalize, SHA256 deduplicate, chunk/embed and persist text with provenance; available to searches immediately."""
    return (
        environment()[3]
        .ingest_text(title, text, source=source, source_type=source_type, url=url)
        .model_dump()
    )


@capability("chroma")
def ingest_document(
    data_base64: str, title: str | None = None, media_type: str = "application/pdf"
):
    """Compatibility path for PDF ingestion; HTTP uploads use staged streaming."""
    if media_type != "application/pdf":
        raise ValueError("A PDF is required")
    data = base64.b64decode(data_base64, validate=True)
    if not data.startswith(b"%PDF-"):
        raise ValueError("A valid PDF is required")
    if get_settings().uses_record_store:
        from time import perf_counter

        started = perf_counter()
        uploaded = documents().upload(data, title, media_type)
        processed = documents().process(uploaded["id"])
        return {
            "pages": processed["pages"],
            "chunks": processed["chunks"],
            "entities": 0,
            "vector_count": processed["chunks"],
            "ms": int((perf_counter() - started) * 1000),
            "duplicate": uploaded["duplicate"],
            "content_hash": uploaded["id"],
        }
    return environment()[3].ingest_pdf(title, data).model_dump()


def _extract_upload(data_base64: str, media_type: str):
    from backend.app.services.document_extract import DocumentError, extract_document

    try:
        data = base64.b64decode(data_base64, validate=True)
    except ValueError:
        raise DocumentError("Invalid document encoding", 400) from None
    return extract_document(data, media_type)


@capability("local_document")
def extract_document(data_base64: str, media_type: str = "application/pdf"):
    """Bounded local PDF/text extraction without Azure requests or persistence."""
    return _extract_upload(data_base64, media_type)


@capability("local_document")
def save_document(
    data_base64: str,
    title: str | None = None,
    media_type: str = "application/pdf",
    ingestion_mode: Literal["structured_graph"] = "structured_graph",
    document_processing: Literal["local", "azure_di"] = "local",
):
    """Operator-controlled extraction and local staging; never creates fake semantic vectors."""
    if ingestion_mode != "structured_graph":
        raise ValueError("Hybrid ingestion is required and cannot be disabled")
    if get_settings().uses_record_store:
        data = base64.b64decode(data_base64, validate=True)
        return documents().upload(data, title, media_type, ingestion_mode, document_processing)
    result = _extract_upload(data_base64, media_type)
    return {
        **documents().save(
            result["text"],
            title,
            "pdf" if media_type == "application/pdf" else "text",
            result["pages"],
        ),
        "pages": result["pages"],
        "method": result["method"],
    }


def _staged_path(upload_token: str):
    import re

    if not re.fullmatch(r"[a-f0-9]{64}", upload_token):
        raise ValueError("Invalid upload token")
    root = (get_settings().local_documents_dir / ".uploads").resolve()
    path = (root / upload_token).resolve()
    if path.parent != root or not path.is_file() or path.is_symlink():
        raise ValueError("Staged upload is unavailable")
    return path


@capability("local_document")
def save_staged_document(
    upload_token: str,
    title: str | None = None,
    media_type: str = "application/pdf",
    ingestion_mode: Literal["structured_graph"] = "structured_graph",
    document_processing: Literal["local", "azure_di"] = "local",
):
    """Persist a server-staged upload without transporting its bytes through MCP."""
    if ingestion_mode != "structured_graph":
        raise ValueError("Hybrid ingestion is required and cannot be disabled")
    if not get_settings().uses_record_store:
        from backend.app.services.document_extract import extract_document_path

        result = extract_document_path(_staged_path(upload_token), media_type)
        return {
            **documents().save(
                result["text"], title,
                "pdf" if media_type == "application/pdf" else "text",
                result["pages"],
            ),
            "ingestion_mode": "structured_graph",
            "graph_link_status": "pending",
        }
    return documents().upload_path(
        _staged_path(upload_token), title, media_type, ingestion_mode, document_processing
    )


@capability("local_document")
def extract_staged_document(
    upload_token: str, media_type: str = "application/pdf",
    title: str | None = None, graph_enrich: bool = False,
):
    """Extract a preview; when explicitly enabled, run Azure DI and publish only graph references.

    The staged original is deleted by the HTTP caller. No original or
    full extracted text is persisted in the preview path.
    """
    path = _staged_path(upload_token)
    if graph_enrich:
        if not get_settings().auto_document_graph_ingestion:
            raise ValueError("Automatic Azure graph ingestion has not been authorized")
        repository = documents()
        if not hasattr(repository, "preview_graph"):
            raise ValueError("Azure graph preview requires the local document repository")
        return repository.preview_graph(path, title or "Unsaved upload", media_type)
    from backend.app.services.document_extract import extract_document_path

    return extract_document_path(path, media_type)


@capability("local_document")
def list_documents():
    """List locally saved document metadata without source text."""
    return documents().list_documents()


@capability("chroma")
def index_document(document_id: str):
    """Explicit operator write: embed a staged document into the configured verified collection."""
    if get_settings().uses_record_store:
        return documents().process(document_id)
    record = documents().get(document_id)
    return (
        environment()[3]
        .ingest_text(
            record["title"],
            record["text"],
            source=record["source_type"],
            source_type=record["source_type"],
        )
        .model_dump()
    )


@capability("local_document")
def reprocess_document(
    document_id: str,
    document_processing: Literal["local", "azure_di"] = "azure_di",
    operator_reviewed_inflight: bool = False,
):
    """Explicitly queue a preserved document for a new extraction generation."""
    return documents().reprocess(
        document_id, document_processing, operator_reviewed_inflight
    )


@capability("local_document")
def enrich_document_graph(document_id: str):
    """Explicitly graph-enrich a saved extraction without repeating Azure processing."""
    repository = documents()
    enrich = getattr(repository, "enrich_graph", None)
    if enrich is None:
        raise ValueError("Graph enrichment is unavailable for this document store")
    return enrich(document_id)


@capability("document_semantic_graph")
async def enrich_document_semantic_graph(document_id: str):
    """Authorized ingestion-stage semantic linking; never exposed to the chat planner."""
    settings = get_settings()
    if not settings.semantic_graph_ingestion_enabled:
        raise ValueError("Semantic graph ingestion is not enabled")
    if not (settings.neo4j_ingestion_enabled and settings.neo4j_uri
            and settings.neo4j_ingestion_user and settings.neo4j_ingestion_password.get_secret_value()):
        raise ValueError("The authorized Neo4j ingestion writer is not configured")
    from neo4j import GraphDatabase
    from backend.app.core.principal import current_principal
    from backend.app.models.provider_factory import get_selectable_provider
    from backend.app.services.capabilities import knowledge_stores
    from backend.app.services.document_semantic_graph import DocumentSemanticGraphService
    from backend.app.store.graph_repo import GraphRepository

    driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_ingestion_user,
        settings.neo4j_ingestion_password.get_secret_value()), connection_timeout=5,
        max_transaction_retry_time=0, connection_acquisition_timeout=5)
    provider = get_selectable_provider(settings)
    try:
        driver.verify_connectivity()
        graph = GraphRepository(driver,
            tenant_provider=lambda: current_principal(settings).tenant_id,
            optional=False, allow_fixtures=False, database=settings.neo4j_database)
        graph.ensure_constraints()
        vector, _, _ = knowledge_stores(settings, connect_graph=False, records=managed_records())
        return await DocumentSemanticGraphService(
            settings, documents(), managed_records(), provider, graph, vector
        ).enrich(document_id)
    finally:
        await provider.aclose()
        driver.close()


@capability("document_store")
def get_document(document_id: str):
    """Read an authorized document's processing status."""
    return documents().get(document_id)


@capability("document_store")
def get_document_original(document_id: str):
    """Authorized immutable source download; no SAS URLs or storage credentials."""
    if not get_settings().uses_record_store:
        raise ValueError("Original-file download is unavailable for locally extracted text")
    record = documents().get(document_id)
    data = documents().blobs.get(document_id, "original")
    return {
        "data_base64": base64.b64encode(data).decode(),
        "title": record["title"],
        "media_type": record["media_type"],
    }


@capability("chroma")
def ingest_url(url: str, max_depth: int | None = None, max_pages: int | None = None):
    """Safely crawl bounded public pages and ingest extracted documents with URL provenance."""
    from backend.app.services.url_crawler import CrawlLimits, UrlCrawler

    s = get_settings()
    if not s.allow_url_ingest or not s.enable_public_web_tools:
        raise URLIngestError(
            "PUBLIC_WEB_DISABLED",
            "Website ingestion is disabled by workspace authorization.",
            403,
        )
    if not s.uses_record_store:
        raise URLIngestError(
            "VECTOR_INDEX_FAILED", "Local website indexing is unavailable.", 503
        )
    depth = s.url_max_depth if max_depth is None else max_depth
    pages = s.url_max_pages if max_pages is None else max_pages
    if not 0 <= depth <= 3 or not 1 <= pages <= 10:
        raise ValueError("Invalid crawl limits")
    try:
        result = UrlCrawler(s).crawl(
            url, CrawlLimits(depth, pages, s.url_max_total_chars, s.url_rate_limit_sec)
        )
    except Exception as exc:
        if hasattr(exc, "code"):
            raise
        logging.getLogger(__name__).warning(
            "URL crawl failed safely (%s)", type(exc).__name__
        )
        if isinstance(exc, ValueError):
            raise URLIngestError("URL_BLOCKED", str(exc), 400) from exc
        raise URLIngestError(
            "WEBSITE_UNAVAILABLE",
            "The website could not be retrieved. Check its address or availability.",
            502,
        ) from exc
    totals = {"chunks": 0, "entities": 0, "vector_count": 0, "ms": 0}
    repository = documents()
    document_ids = []
    states = []
    failed_pages = 0
    duplicates = []
    retrieved_at = datetime.now(timezone.utc).isoformat()
    for page in result.pages:
        try:
            uploaded = repository.upload(
                page.content.encode("utf-8"),
                page.title,
                "text/plain",
                "structured_graph",
                "local",
                source_type="url",
                source_url=page.url,
                canonical_url=page.canonical_url or page.url,
                retrieved_at=retrieved_at,
            )
            state = repository.process(uploaded["id"])
            document_ids.append(uploaded["id"])
            states.append(state)
            duplicates.append(bool(uploaded.get("duplicate")))
            totals["chunks"] += int(state.get("chunks", 0))
            totals["vector_count"] += int(state.get("chunks", 0))
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "URL page indexing failed safely (%s)", type(exc).__name__
            )
            failed_pages += 1
    if not states:
        raise URLIngestError(
            "VECTOR_INDEX_FAILED",
            "The website was retrieved, but local knowledge indexing failed.",
            502,
        )
    local_ready = all(state.get("local_index_ready") for state in states)
    graph_link_status = (
        "linked" if all(state.get("graph_link_status") == "linked" for state in states)
        else "pending"
    )
    semantic_statuses = {state.get("semantic_graph_status", "pending") for state in states}
    semantic_graph_status = semantic_statuses.pop() if len(semantic_statuses) == 1 else "pending"
    status = "partial" if failed_pages else "ready" if graph_link_status == "linked" else "graph_pending"
    return {
        **totals,
        "pages": len(states),
        "failed_pages": failed_pages,
        "skipped_urls": result.skipped_urls,
        "document_ids": document_ids,
        "duplicate": bool(duplicates) and all(duplicates),
        "content_hash": document_ids[0] if len(document_ids) == 1 else None,
        "local_index_ready": local_ready,
        "graph_link_status": graph_link_status,
        "semantic_graph_status": semantic_graph_status,
        "status": status,
    }


@capability("sqlite_memory", 1)
def remember_fact(fact: MemoryFact):
    """Save a confirmed reusable human/trusted-system fact with provenance; LLM guesses are forbidden."""
    memory().save_fact(fact.model_dump())
    return fact.model_dump()


@capability("sqlite_memory", 1)
def search_memory(subject: str, query: str | None = None):
    """Search confirmed semantic facts across cases; retain timestamps and conflicting values."""
    return [
        MemoryFact.model_validate(f).model_dump()
        for f in memory().search_facts(subject, query)
        if f.get("source") in {"human_confirmed", "trusted_tool", "resolved_outcome"}
    ]


@capability("sqlite_memory", 1)
def search_remembered_facts(subject: str, query: str | None = None):
    """Compatibility name for confirmed cross-case semantic fact search."""
    return [
        MemoryFact.model_validate(f).model_dump()
        for f in memory().search_facts(subject, query)
        if f.get("source") in {"human_confirmed", "trusted_tool", "resolved_outcome"}
    ]


@capability("sqlite_memory", 1)
def save_case_note(
    case_id: str,
    text: str,
    source: str = "agent",
    tool: str = "save_case_note",
    index_knowledge: bool = False,
    event_key: str | None = None,
):
    """Record an actual case action/human response. Optionally index a useful technician note as knowledge."""
    memory().record_event(case_id, tool, source, text, event_key)
    if index_knowledge:
        environment()[3].ingest_text("Case note", text, source=case_id, source_type="case_note")
    return {"saved": True, "case_id": case_id}


@capability("sqlite_memory", 1)
def get_case_history(case_id: str):
    """Read episodic memory: actual events/actions and technician answers for one case."""
    return memory().list_events(case_id)


@capability("sqlite_memory", 1)
def get_case(case_id: str):
    """Read operational case metadata/state. Empty items indicate a missing case."""
    return memory().load_case(case_id)


@capability("sqlite_memory", 1)
def list_cases():
    """List saved case metadata and current assessment snapshots."""
    return [memory().load_case(cid) for cid in memory().list_cases()]


@capability("sqlite_memory", 1)
def save_case(case_id: str, state: dict[str, Any]):
    """Persist the application's canonical case snapshot; not available to LLM plans."""
    if state.get("case_id") != case_id:
        raise ValueError("Case identifier mismatch")
    memory().save_case(case_id, state)
    return state


@capability("environment", 1)
def list_available_context(question: str | None = None):
    """Discover bounded tenant-owned knowledge metadata and available services."""
    vector, _, driver, _, _ = environment()
    catalog = []
    rows = []
    if get_settings().uses_record_store:
        import re

        terms = {
            value.casefold()
            for value in re.findall(r"[\w-]+", question or "")
            if len(value) > 2
        }
        rows = documents().list_documents()
        rows.sort(
            key=lambda row: (
                -sum(term in str(row.get("title") or "").casefold() for term in terms),
                str(row.get("title") or "").casefold(),
            )
        )
        catalog = [
            {
                "id": row.get("id"),
                "title": row.get("title"),
                "source_type": row.get("source_type"),
                "status": row.get("status"),
                "pages": row.get("pages"),
                "tables": row.get("tables", 0),
                "ingestion_mode": row.get("ingestion_mode"),
                "graph_link_status": row.get("graph_link_status"),
                "graph_nodes": row.get("graph_nodes"),
                "graph_relationships": row.get("graph_relationships"),
                "version": row.get("version", 1),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            }
            for row in rows[:20]
        ]
    return {
        "knowledge_chunks": vector.count(),
        "local_documents_available": bool(catalog),
        "document_catalog": catalog,
        "document_catalog_bounded": len(catalog) < len(rows) if get_settings().uses_record_store else False,
        # Complete tenant-owned title inventory for application-side document
        # resolution; the agent removes it before building model context.
        "document_inventory": [
            {
                "id": row.get("id"),
                "title": row.get("title"),
                "family": row.get("family"),
                "version": row.get("version", 1),
            }
            for row in rows
        ],
        "chroma": vector.ping(),
        "aura_connected": driver is not None,
        **(fixtures.inventory() if get_settings().enable_local_fixtures else {}),
        "fixture_source_system": "local_fixture",
        "memory": "sqlite_memory",
        "web": "public_web" if get_settings().enable_public_web_tools else "unavailable",
    }


@capability("chroma")
def resolve_graph_references(question: str, object_ids: list[str]):
    """Application-controlled: hydrate graph object references into local passages.

    Only tenant-owned passages are returned, ranked against ``question``.
    """
    if not 1 <= len(object_ids) <= 500:
        raise ValueError("object_ids must contain between 1 and 500 references")
    repository = documents()
    resolve = getattr(getattr(repository, "search_store", None), "resolve_object_ids", None)
    if resolve is None:
        return {
            "available": False,
            "reason": "Graph-reference hydration requires local document persistence.",
        }
    embedding = repository.embeddings.embed_texts([question])[0]
    return resolve(object_ids, embedding)


@capability("local_storage", 1)
def get_local_storage_status() -> dict[str, bool]:
    """Probe local SQLite/Chroma only; no embedding, identity, Azure or Aura requests."""
    from contextlib import closing

    from backend.app.services.capabilities import knowledge_stores

    vector, _, _ = knowledge_stores(get_settings(), connect_graph=False)
    if get_settings().uses_record_store:
        managed_records().query("case", limit=1)
        return {"chroma": vector.ping(), "sqlite": True, "aura_connected": False}
    with closing(memory()._connect()) as connection:
        sqlite_ready = connection.execute("SELECT 1").fetchone()[0] == 1
    return {
        "chroma": vector.ping(),
        "sqlite": sqlite_ready,
        "aura_connected": bool(environment()[2]) if environment.cache_info().currsize else False,
    }


if __name__ == "__main__":
    server.run(transport="stdio")
