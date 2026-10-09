"""DeskMate's capability boundary, exposed through the official stdio MCP SDK."""

from functools import lru_cache
from typing import Any
from mcp.server.fastmcp import FastMCP
from backend.app.agent.memory import CaseMemoryStore
from backend.app.agent.models import MemoryFact
from backend.app.core.config import get_settings
from backend.app.repositories.fixtures import FixtureRepository

server = FastMCP("deskmate")
fixtures = FixtureRepository()


@lru_cache
def memory():
    return CaseMemoryStore(get_settings().case_db_path)


@lru_cache
def retriever():
    from backend.app.adapters.embeddings import get_embedding_provider
    from backend.app.rag.retrieve import Retriever
    from backend.app.services.capabilities import knowledge_stores

    s = get_settings()
    vector, graph, _ = knowledge_stores(s)
    return Retriever(s, vector, graph, get_embedding_provider(s))


@server.tool()
def search_knowledge(question: str, mode: str = "HYBRID", top_k: int = 6) -> dict:
    """Retrieve Chroma knowledge with optional Aura graph context."""
    if mode.upper() not in {"VECTOR", "GRAPH", "HYBRID"}:
        raise ValueError("mode must be VECTOR, GRAPH or HYBRID")
    if not 1 <= top_k <= 30:
        raise ValueError("top_k must be between 1 and 30")
    r = retriever()
    method = {"VECTOR": r.vector_search, "GRAPH": r.graph_search, "HYBRID": r.hybrid_search}[
        mode.upper()
    ]
    return {"result": method(question, top_k)}


@server.tool()
def search_web(query: str, max_results: int = 5) -> dict:
    """Search the public web; results remain untrusted external evidence."""
    from ddgs import DDGS

    if not query.strip():
        return {"result": []}
    if not 1 <= max_results <= 10:
        raise ValueError("max_results must be between 1 and 10")
    results = DDGS(timeout=10).text(query, max_results=max_results)
    return {
        "result": [
            {
                "title": x.get("title", ""),
                "url": x.get("href", ""),
                "snippet": x.get("body", ""),
                "source_system": "public_web",
            }
            for x in results
            if x.get("href")
        ]
    }


@server.tool()
def fetch_web_page(url: str) -> dict:
    """Safely fetch and extract a bounded public HTML page."""
    from backend.app.services.web import fetch_page

    return {"result": fetch_page(url, max_bytes=get_settings().web_max_bytes)}


@server.tool()
def search_previous_tickets(
    query: str, user: str | None = None, company: str | None = None, site: str | None = None
) -> dict:
    """Search previous incidents from the explicitly labeled local fixtures."""
    return {"result": fixtures.search_previous_tickets(query, user, company, site)}


@server.tool()
def get_user_context(user_name: str, company: str | None = None) -> dict:
    """Read fixture user context; current location and update state may be unknown."""
    return {"result": fixtures.get_user_context(user_name, company)}


@server.tool()
def get_company_context(company: str) -> dict:
    """Read fixture company context."""
    return {"result": fixtures.get_company_context(company)}


@server.tool()
def get_site_context(company: str, site: str) -> dict:
    """Read context for one fixture site."""
    return {"result": fixtures.get_site_context(company, site)}


@server.tool()
def get_device_context(device_or_user: str) -> dict:
    """Read fixture device context."""
    return {"result": fixtures.get_device_context(device_or_user)}


@server.tool()
def get_active_alerts(company: str, site: str | None = None, product: str | None = None) -> dict:
    """Read active fixture alerts including technician acknowledgement."""
    return {"result": fixtures.get_active_alerts(company, site, product)}


@server.tool()
def get_recent_changes(company: str, site: str | None = None, product: str | None = None) -> dict:
    """Read recent fixture configuration changes."""
    return {"result": fixtures.get_recent_changes(company, site, product)}


@server.tool()
def save_case_note(case_id: str, text: str, source: str = "agent") -> dict:
    """Append a case event in the shared SQLite memory store."""
    memory().record_event(case_id, "save_case_note", source, text)
    return {"result": {"saved": True, "case_id": case_id, "source_system": "sqlite_memory"}}


@server.tool()
def remember_fact(fact: dict[str, Any]) -> dict:
    """Persist a provenance-validated confirmed fact, never a model guess."""
    parsed = MemoryFact.model_validate(fact)
    memory().save_fact(parsed.model_dump())
    return {"result": parsed.model_dump()}


@server.tool()
def search_remembered_facts(subject: str, query: str | None = None) -> dict:
    """Find confirmed facts about a subject across all cases."""
    return {"result": memory().search_facts(subject, query)}


if __name__ == "__main__":
    server.run(transport="stdio")
