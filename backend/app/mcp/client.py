from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

logger = logging.getLogger("service-desk")
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class DeskMateMCPClient:
    """Application-level client that performs real MCP tool calls into the DeskMate server."""

    def __init__(self, *, server_command: str | None = None, cwd: str | None = None) -> None:
        self.server_command = server_command or sys.executable
        self.cwd = cwd or str(PROJECT_ROOT)
        self._stdio_cm = None
        self._session = None
        self._read = None
        self._write = None

    async def _ensure_session(self) -> None:
        if self._session is not None:
            return
        params_obj = StdioServerParameters(
            command=self.server_command,
            args=["-m", "backend.app.mcp.server"],
            cwd=self.cwd,
            env=None,
        )
        self._stdio_cm = stdio_client(params_obj)
        self._read, self._write = await self._stdio_cm.__aenter__()
        self._session = ClientSession(self._read, self._write)
        await self._session.__aenter__()
        await self._session.initialize()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.__aexit__(None, None, None)
            self._session = None
        if self._stdio_cm is not None:
            await self._stdio_cm.__aexit__(None, None, None)
            self._stdio_cm = None
        self._read = None
        self._write = None

    async def call_tool_async(self, tool_name: str, params: dict[str, Any]) -> list[dict[str, Any]] | dict[str, Any]:
        await self._ensure_session()
        result = await self._session.call_tool(tool_name, params)
        payload = self._coerce_tool_result(result)
        logger.info("MCP client received %s payload entries from %s", len(payload) if isinstance(payload, list) else 1, tool_name)
        return payload

    def search_knowledge(self, question: str, mode: str, top_k: int) -> list[dict[str, Any]]:
        """Call the DeskMate MCP server's search_knowledge tool."""
        logger.info("MCP client calling search_knowledge question='%s' mode=%s top_k=%s", question, mode, top_k)
        return asyncio.run(self.call_tool_async("search_knowledge", {"question": question, "mode": mode, "top_k": top_k}))

    def search_web(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        """Wrap the public web-search MCP tool."""
        logger.info("MCP client calling search_web query='%s' max_results=%s", query, max_results)
        return asyncio.run(self.call_tool_async("search_web", {"query": query, "max_results": max_results}))

    def fetch_web_page(self, url: str) -> dict[str, Any]:
        """Fetch a single public page through the web MCP tool."""
        logger.info("MCP client calling fetch_web_page url='%s'", url)
        result = asyncio.run(self.call_tool_async("fetch_web_page", {"url": url}))
        return result[0] if isinstance(result, list) and result else {}

    async def search_previous_tickets_async(self, query: str, user: str | None = None, company: str | None = None, site: str | None = None) -> list[dict[str, Any]]:
        return await self.call_tool_async("search_previous_tickets", {"query": query, "user": user, "company": company, "site": site})

    async def get_user_context_async(self, user_name: str, company: str | None = None) -> dict[str, Any]:
        return await self.call_tool_async("get_user_context", {"user_name": user_name, "company": company})

    async def get_company_context_async(self, company: str) -> dict[str, Any]:
        return await self.call_tool_async("get_company_context", {"company": company})

    async def get_site_context_async(self, company: str, site: str) -> dict[str, Any]:
        return await self.call_tool_async("get_site_context", {"company": company, "site": site})

    async def get_device_context_async(self, device_or_user: str) -> dict[str, Any]:
        return await self.call_tool_async("get_device_context", {"device_or_user": device_or_user})

    async def get_active_alerts_async(self, company: str, site: str | None = None, product: str | None = None) -> list[dict[str, Any]]:
        return await self.call_tool_async("get_active_alerts", {"company": company, "site": site, "product": product})

    async def get_recent_changes_async(self, company: str, site: str | None = None, product: str | None = None) -> list[dict[str, Any]]:
        return await self.call_tool_async("get_recent_changes", {"company": company, "site": site, "product": product})

    async def save_case_note_async(self, case_id: str, text: str, source: str) -> dict[str, Any]:
        return await self.call_tool_async("save_case_note", {"case_id": case_id, "text": text, "source": source})

    async def remember_fact_async(self, subject: str, predicate: str, value: str, source: str, observed_at: str) -> dict[str, Any]:
        return await self.call_tool_async("remember_fact", {"subject": subject, "predicate": predicate, "value": value, "source": source, "observed_at": observed_at})

    async def search_remembered_facts_async(self, subject: str, query: str | None = None) -> list[dict[str, Any]]:
        return await self.call_tool_async("search_remembered_facts", {"subject": subject, "query": query})

    @staticmethod
    def _coerce_tool_result(result: Any) -> list[dict[str, Any]] | dict[str, Any]:
        if hasattr(result, "structuredContent") and result.structuredContent is not None:
            payload = result.structuredContent
            if isinstance(payload, list):
                return payload
            if isinstance(payload, dict):
                for key in ("result", "results", "content"):
                    if key in payload:
                        value = payload[key]
                        if isinstance(value, list):
                            return value
                        if isinstance(value, dict):
                            return value.get("results", value)
                return payload

        if hasattr(result, "content"):
            content = result.content
            if isinstance(content, list):
                for item in content:
                    if hasattr(item, "text"):
                        text = getattr(item, "text")
                        if isinstance(text, str):
                            try:
                                parsed = json.loads(text)
                                if isinstance(parsed, list):
                                    return parsed
                                if isinstance(parsed, dict):
                                    if "result" in parsed:
                                        return parsed["result"]
                                    if "results" in parsed:
                                        return parsed["results"]
                                    return parsed
                            except json.JSONDecodeError:
                                continue

        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for key in ("result", "results", "content"):
                if key in result:
                    value = result[key]
                    if isinstance(value, list):
                        return value
                    if isinstance(value, dict):
                        return value.get("results", value)
            return result
        raise ValueError("MCP tool result did not contain structured payload data")
