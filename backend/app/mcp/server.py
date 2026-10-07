from __future__ import annotations

import logging
from typing import Any

import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
from mcp.server.fastmcp import FastMCP

from backend.app.adapters.embeddings import get_embedding_provider
from backend.app.core.config import get_settings
from backend.app.rag.retrieve import Retriever
from backend.app.store.graph_repo import GraphRepository, InMemoryGraphRepository
from backend.app.store.vector_chroma import InMemoryVectorStore, VectorChromaStore

logger = logging.getLogger("service-desk")
server = FastMCP("deskmate")


def _build_retriever() -> Retriever:
    settings = get_settings()

    vector_store = VectorChromaStore(path=str(settings.chroma_dir))
    graph_repo: Any = InMemoryGraphRepository()

    neo4j_uri = getattr(settings, "neo4j_uri", "") or ""
    if neo4j_uri.startswith(("neo4j+s://", "neo4j+ssc://")):
        try:
            from neo4j import GraphDatabase

            driver = GraphDatabase.driver(neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
            driver.verify_connectivity()
            graph_repo = GraphRepository(driver)
            graph_repo.ensure_constraints()
        except Exception as exc:  # pragma: no cover - runtime-dependent connectivity
            logger.warning("Neo4j Aura unavailable in MCP server (%s); using in-memory graph fallback", exc)
            graph_repo = InMemoryGraphRepository()

    embedding_provider = get_embedding_provider(settings)
    return Retriever(
        settings=settings,
        vector_store=vector_store,
        graph_repo=graph_repo,
        embedding_provider=embedding_provider,
    )


@server.tool(name="search_knowledge")
def search_knowledge(question: str, mode: str = "VECTOR", top_k: int = 6) -> dict[str, list[dict[str, Any]]]:
    """Execute the existing Retriever through the MCP boundary."""
    logger.info("MCP server received search_knowledge question='%s' mode=%s top_k=%s", question, mode, top_k)
    normalized = (mode or "VECTOR").upper()
    if normalized not in {"VECTOR", "GRAPH", "HYBRID"}:
        raise ValueError("mode must be one of VECTOR, GRAPH, HYBRID")

    retriever = _build_retriever()
    if normalized == "GRAPH":
        results = retriever.graph_search(question, top_k)
    elif normalized == "HYBRID":
        results = retriever.hybrid_search(question, top_k)
    else:
        results = retriever.vector_search(question, top_k)

    cleaned: list[dict[str, Any]] = []
    for result in results:
        metadata = result.get("metadata", {}) or {}
        cleaned.append(
            {
                "id": result.get("id"),
                "text": result.get("text"),
                "score": result.get("score"),
                "metadata": {
                    "doc_id": metadata.get("doc_id"),
                    "title": metadata.get("title"),
                    "source": metadata.get("source"),
                    "ord": metadata.get("ord"),
                },
            }
        )

    logger.info("MCP server returned %s chunks from Retriever", len(cleaned))
    return {"result": cleaned}


@server.tool(name="search_web")
def search_web(query: str, max_results: int = 5) -> dict[str, list[dict[str, Any]]]:
    """Return a small set of public search results for web fallback."""
    logger.info("MCP server received search_web query='%s' max_results=%s", query, max_results)
    if not query or not query.strip():
        return {"result": []}
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=max_results))
    cleaned = []
    for item in results:
        href = item.get("href") or item.get("url") or ""
        if not href:
            continue
        cleaned.append(
            {
                "title": item.get("title") or "Web result",
                "url": href,
                "snippet": item.get("body") or item.get("snippet") or "",
            }
        )
    logger.info("MCP server returned %s web hits", len(cleaned))
    return {"result": cleaned}


@server.tool(name="fetch_web_page")
def fetch_web_page(url: str) -> dict[str, Any]:
    """Fetch and extract clean text from a public web page for ingestion."""
    logger.info("MCP server received fetch_web_page url='%s'", url)
    if not url:
        raise ValueError("url must not be empty")
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    title = (soup.title.get_text(" ", strip=True) if soup.title else "") or url
    text = soup.get_text(" ", strip=True)
    return {"result": {"url": url, "title": title, "text": text}}


if __name__ == "__main__":
    logger.info("Starting DeskMate MCP server over stdio")
    server.run(transport="stdio")
