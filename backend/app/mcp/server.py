"""Typed MCP capabilities. Storage and external systems live only behind this boundary."""

import asyncio
import base64
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
                        "search_web", "search_news", "fetch_web_page", "ingest_url"
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
                        result = await asyncio.to_thread(fn, *args, **kwargs)
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
                        {"http_status": exc.status_code} if hasattr(exc, "status_code") else None
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
):
    """Semantic document/KB retrieval. Use for factual questions, resumes, PDFs and troubleshooting documentation."""
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
        rows = {"VECTOR": r.vector_search, "GRAPH": r.graph_search, "HYBRID": r.hybrid_search}[
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
    """Extract and ingest an uploaded PDF, limited to 5 MB. Input bytes are base64, never a filesystem path."""
    if media_type != "application/pdf" or len(data_base64) > 6_666_672:
        raise ValueError("A PDF of at most 5 MB is required")
    data = base64.b64decode(data_base64, validate=True)
    if len(data) > 5_000_000 or not data.startswith(b"%PDF-"):
        raise ValueError("A valid PDF of at most 5 MB is required")
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

    if len(data_base64) > 6_666_672:
        raise DocumentError("Document exceeds 5 MB", 413)
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
def save_document(data_base64: str, title: str | None = None, media_type: str = "application/pdf"):
    """Operator-controlled extraction and local staging; never creates fake semantic vectors."""
    if get_settings().uses_record_store:
        data = base64.b64decode(data_base64, validate=True)
        if len(data) > 5_000_000:
            raise ValueError("Document exceeds 5 MB")
        return documents().upload(data, title, media_type)
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
    if not s.allow_url_ingest:
        raise ValueError("URL ingestion is disabled")
    depth = s.url_max_depth if max_depth is None else max_depth
    pages = s.url_max_pages if max_pages is None else max_pages
    if not 0 <= depth <= 3 or not 1 <= pages <= 10:
        raise ValueError("Invalid crawl limits")
    result = UrlCrawler(s).crawl(
        url, CrawlLimits(depth, pages, s.url_max_total_chars, s.url_rate_limit_sec)
    )
    totals = {"chunks": 0, "entities": 0, "vector_count": 0, "ms": 0}
    for page in result.pages:
        item = environment()[3].ingest_text(
            page.title, page.content, source=page.url, source_type="url", url=page.url
        )
        for key in totals:
            totals[key] += getattr(item, key)
    return {**totals, "pages": len(result.pages), "skipped_urls": result.skipped_urls}


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
def list_available_context():
    """Discover available datasets/services, fixture provenance, graph availability, and knowledge document counts."""
    vector, _, driver, _, _ = environment()
    return {
        "knowledge_chunks": vector.count(),
        "chroma": vector.ping(),
        "aura_connected": driver is not None,
        **(fixtures.inventory() if get_settings().enable_local_fixtures else {}),
        "fixture_source_system": "local_fixture",
        "memory": "sqlite_memory",
        "web": "public_web",
    }


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
