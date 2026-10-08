from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from langchain_core.tools import tool

from backend.app.agent.memory import CaseMemoryStore
from backend.app.mcp.client import DeskMateMCPClient


class AgentToolHarness:
    """LangChain tool wrapper for the real MCP capability boundary."""

    def __init__(self, mcp_client: DeskMateMCPClient, memory_store: CaseMemoryStore | None = None) -> None:
        self.mcp = mcp_client
        self.memory = memory_store

    async def async_tools(self) -> Dict[str, Any]:
        return {
            "search_previous_tickets": self.search_previous_tickets,
            "get_user_context": self.get_user_context,
            "get_company_context": self.get_company_context,
            "get_site_context": self.get_site_context,
            "get_device_context": self.get_device_context,
            "get_active_alerts": self.get_active_alerts,
            "get_recent_changes": self.get_recent_changes,
            "search_knowledge": self.search_knowledge,
            "search_web": self.search_web,
            "fetch_web_page": self.fetch_web_page,
            "save_case_note": self.save_case_note,
            "remember_fact": self.remember_fact,
            "search_remembered_facts": self.search_remembered_facts,
        }

    @tool
    async def search_previous_tickets(self, query: str, user: str | None = None, company: str | None = None, site: str | None = None) -> str:
        """Search previous tickets relevant to the query and optional user/company/site."""
        result = await self.mcp.search_previous_tickets_async(query, user=user, company=company, site=site)
        return json.dumps(result, default=str)

    @tool
    async def get_user_context(self, user_name: str, company: str | None = None) -> str:
        """Return contextual information about a user from external systems."""
        result = await self.mcp.get_user_context_async(user_name, company=company)
        return json.dumps(result, default=str)

    @tool
    async def get_company_context(self, company: str) -> str:
        """Return contextual information about a company from external systems."""
        result = await self.mcp.get_company_context_async(company)
        return json.dumps(result, default=str)

    @tool
    async def get_site_context(self, company: str, site: str) -> str:
        """Return contextual information about a specific site for a company."""
        result = await self.mcp.get_site_context_async(company, site)
        return json.dumps(result, default=str)

    @tool
    async def get_device_context(self, device_or_user: str) -> str:
        """Return device-level context or recent device activity."""
        result = await self.mcp.get_device_context_async(device_or_user)
        return json.dumps(result, default=str)

    @tool
    async def get_active_alerts(self, company: str, site: str | None = None, product: str | None = None) -> str:
        """Return active alerts for a company/site/product."""
        result = await self.mcp.get_active_alerts_async(company, site=site, product=product)
        return json.dumps(result, default=str)

    @tool
    async def get_recent_changes(self, company: str, site: str | None = None, product: str | None = None) -> str:
        """Return recent changes such as updates or configuration changes."""
        result = await self.mcp.get_recent_changes_async(company, site=site, product=product)
        return json.dumps(result, default=str)

    @tool
    async def search_knowledge(self, question: str, mode: str = "HYBRID", top_k: int = 6) -> str:
        """Search the knowledge base (vector/graph/hybrid) for relevant documents."""
        result = self.mcp.search_knowledge(question, mode, top_k)
        return json.dumps(result, default=str)

    @tool
    async def search_web(self, query: str, max_results: int = 5) -> str:
        """Run a public web search for the query and return summarized results."""
        result = self.mcp.search_web(query, max_results=max_results)
        return json.dumps(result, default=str)

    @tool
    async def fetch_web_page(self, url: str) -> str:
        """Fetch a web page for scraping or citation purposes."""
        result = self.mcp.fetch_web_page(url)
        return json.dumps(result, default=str)

    @tool
    async def save_case_note(self, case_id: str, text: str, source: str) -> str:
        """Persist a short note into the case event timeline."""
        if self.memory is not None:
            self.memory.record_event(case_id, None, source, text)
        return json.dumps({"case_id": case_id, "source": source, "saved": True}, default=str)

    @tool
    async def remember_fact(self, subject: str, predicate: str, value: str, source: str, observed_at: str | None = None) -> str:
        """Store a fact in the case memory store for later retrieval."""
        if self.memory is not None:
            fact = {
                "subject": subject,
                "predicate": predicate,
                "value": value,
                "source": source,
                "source_system": "local_fixture",
                "observed_at": observed_at or datetime.now(timezone.utc).isoformat(),
                "confidence": 0.9,
                "reliability": "confirmed",
                "metadata": {},
            }
            self.memory.save_fact(fact)
        return json.dumps(fact, default=str)

    @tool
    async def search_remembered_facts(self, subject: str, query: str | None = None) -> str:
        """Search persisted facts in the case memory store."""
        if self.memory is None:
            return json.dumps([])
        return json.dumps(self.memory.search_facts(subject, query=query), default=str)
