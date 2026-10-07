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

    def search_knowledge(self, question: str, mode: str, top_k: int) -> list[dict[str, Any]]:
        """Call the DeskMate MCP server's search_knowledge tool."""
        logger.info("MCP client calling search_knowledge question='%s' mode=%s top_k=%s", question, mode, top_k)
        return asyncio.run(self._call_tool_async("search_knowledge", {"question": question, "mode": mode, "top_k": top_k}))

    def search_web(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        """Wrap the public web-search MCP tool."""
        logger.info("MCP client calling search_web query='%s' max_results=%s", query, max_results)
        return asyncio.run(self._call_tool_async("search_web", {"query": query, "max_results": max_results}))

    def fetch_web_page(self, url: str) -> dict[str, Any]:
        """Fetch a single public page through the web MCP tool."""
        logger.info("MCP client calling fetch_web_page url='%s'", url)
        result = asyncio.run(self._call_tool_async("fetch_web_page", {"url": url}))
        return result[0] if isinstance(result, list) and result else {}

    async def _call_tool_async(self, tool_name: str, params: dict[str, Any]) -> list[dict[str, Any]] | dict[str, Any]:
        params_obj = StdioServerParameters(
            command=self.server_command,
            args=["-m", "backend.app.mcp.server"],
            cwd=self.cwd,
            env=None,
        )
        try:
            async with stdio_client(params_obj) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, params)
                    payload = self._coerce_tool_result(result)
                    logger.info("MCP client received %s payload entries from %s", len(payload) if isinstance(payload, list) else 1, tool_name)
                    return payload
        except Exception as exc:  # pragma: no cover - runtime protocol failure path
            logger.exception("MCP tool call failed in DeskMateMCPClient for %s", tool_name)
            raise RuntimeError(f"MCP tool call failed for {tool_name}: {exc}") from exc

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
