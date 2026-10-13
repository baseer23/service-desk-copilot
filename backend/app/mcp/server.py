"""Typed MCP capabilities. Storage and external systems live only behind this boundary."""

import asyncio
import base64
from datetime import datetime, timezone
from functools import lru_cache, wraps
from typing import Any
from mcp.server.fastmcp import FastMCP
from backend.app.agent.memory import CaseMemoryStore
from backend.app.agent.models import CapabilityResult, MemoryFact
from backend.app.voice.auth import Actor
from backend.app.voice.repository import SQLiteVoiceConversationRepository
from backend.app.core.config import get_settings
from backend.app.repositories.fixtures import FixtureRepository

server = FastMCP("deskmate")
fixtures = FixtureRepository()


def capability(source_system, reliability=0.9):
    def decorate(fn):
        @wraps(fn)
        async def invoke(*args, **kwargs):
            try:
                result = await asyncio.to_thread(fn, *args, **kwargs)
                rows = result if isinstance(result, list) else [result] if result else []
                status = (
                    "unavailable"
                    if isinstance(result, dict) and result.get("available") is False
                    else "ok"
                )
                return CapabilityResult(
                    tool=fn.__name__,
                    source_system=source_system,
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
                    source_system=source_system,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    reliability=0,
                    status="error",
                    error=message,
                    result=(
                        {"http_status": exc.status_code} if hasattr(exc, "status_code") else None
                    ),
                )

        invoke.__annotations__ = {**fn.__annotations__, "return": CapabilityResult}
        import inspect

        invoke.__signature__ = inspect.signature(fn).replace(return_annotation=CapabilityResult)
        server.tool()(invoke)
        return invoke

    return decorate


@lru_cache
def memory():
    return CaseMemoryStore(get_settings().case_db_path)


@lru_cache
def environment():
    from backend.app.services.capabilities import knowledge_stores
    from backend.app.adapters.embeddings import get_embedding_provider
    from backend.app.services.ingest_service import IngestService
    from backend.app.rag.retrieve import Retriever

    s = get_settings()
    vector, graph, driver = knowledge_stores(s)
    if driver:
        graph.upsert_relationships(fixtures.relationships())
    embedding = get_embedding_provider(s)
    return (
        vector,
        graph,
        driver,
        IngestService(s, vector, graph, embedding),
        Retriever(s, vector, graph, embedding),
    )


@capability("chroma")
def search_knowledge(question: str, mode: str = "HYBRID", top_k: int = 6):
    """Semantic document/KB retrieval. Use for factual questions, resumes, PDFs and troubleshooting documentation."""
    if mode.upper() not in {"VECTOR", "GRAPH", "HYBRID"}:
        raise ValueError("mode must be VECTOR, GRAPH or HYBRID")
    if not 1 <= top_k <= 30:
        raise ValueError("top_k must be between 1 and 30")
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
    if get_settings().azure_live_test_mode:
        raise ValueError("Public web research is disabled during Azure live acceptance")
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


@capability("public_web", 0.7)
def fetch_web_page(url: str):
    """Fetch/extract a bounded public HTML page with SSRF and redirect validation."""
    if get_settings().azure_live_test_mode:
        raise ValueError("Public web research is disabled during Azure live acceptance")
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
    if get_settings().azure_live_test_mode and (
        source != "paste" or source_type != "paste" or url or len(text.encode()) > 1024
    ):
        raise ValueError("Azure acceptance allows only a tiny pasted fictional document")
    if get_settings().azure_live_test_mode:
        from backend.app.core.azure_usage import AzureUsageGuard

        AzureUsageGuard(get_settings()).claim_fixture(text)
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
    if get_settings().azure_live_test_mode:
        raise ValueError("Azure acceptance allows only a tiny pasted fictional document")
    if media_type != "application/pdf" or len(data_base64) > 6_666_672:
        raise ValueError("A PDF of at most 5 MB is required")
    data = base64.b64decode(data_base64, validate=True)
    if len(data) > 5_000_000 or not data.startswith(b"%PDF-"):
        raise ValueError("A valid PDF of at most 5 MB is required")
    return environment()[3].ingest_pdf(title, data).model_dump()


@capability("chroma")
def ingest_url(url: str, max_depth: int | None = None, max_pages: int | None = None):
    """Safely crawl bounded public pages and ingest extracted documents with URL provenance."""
    from backend.app.services.url_crawler import UrlCrawler, CrawlLimits

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
        **fixtures.inventory(),
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
    with closing(memory()._connect()) as connection:
        sqlite_ready = connection.execute("SELECT 1").fetchone()[0] == 1
    return {
        "chroma": vector.ping(),
        "sqlite": sqlite_ready,
        "aura_connected": bool(environment()[2]) if environment.cache_info().currsize else False,
    }


@lru_cache(maxsize=1)
def voice_repository() -> SQLiteVoiceConversationRepository:
    from backend.app.voice.repository import SQLiteVoiceConversationRepository

    return SQLiteVoiceConversationRepository(get_settings().case_db_path)


def voice_actor(identity_token: str) -> Actor:
    from backend.app.voice.auth import verify_actor

    return verify_actor(get_settings(), identity_token)


@capability("voice_support", 1)
def get_voice_session(session_id: str, identity_token: str) -> dict[str, Any]:
    """Owner-scoped conversation; application-controlled identity is mandatory."""
    return voice_repository().get(session_id, voice_actor(identity_token)).public()


@capability("voice_support", 1)
def get_voice_session_summary(session_id: str, identity_token: str) -> dict[str, Any]:
    """Read the authenticated owner’s concise support summary."""
    state = voice_repository().get(session_id, voice_actor(identity_token))
    return {
        "session_id": session_id,
        "summary": state.final_summary or state.issue_summary,
        "resolution_status": state.resolution_status,
        "case_id": state.case_id,
    }


@capability("voice_support", 1)
def list_voice_session_evidence(session_id: str, identity_token: str) -> list[dict[str, Any]]:
    """Read canonical evidence linked to an owned browser support session."""
    state = voice_repository().get(session_id, voice_actor(identity_token))
    return [e.model_dump(mode="json") for e in state.evidence]


@capability("voice_support", 1)
def create_case_from_voice_session(session_id: str, identity_token: str) -> dict[str, str]:
    """Explicit user action; graph plans cannot select this write capability."""
    return {
        "case_id": voice_repository().create_case(session_id, voice_actor(identity_token), memory())
    }


if __name__ == "__main__":
    server.run(transport="stdio")
