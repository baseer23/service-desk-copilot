"""Async MCP transport with one task owning the complete stdio lifetime."""

from __future__ import annotations
import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class MCPToolError(RuntimeError):
    """Server tool failure; the connection remains usable."""


class MCPConnectionError(RuntimeError):
    """Transport or initialization failed."""


class DeskMateMCPClient:
    def __init__(self, settings=None, *, server_command=None, cwd=None):
        from backend.app.core.config import get_settings

        self.settings = settings or get_settings()
        self.server_command = server_command or sys.executable
        self.cwd = cwd or str(PROJECT_ROOT)
        self._lock = asyncio.Lock()
        self._task = None
        self._queue = asyncio.Queue()
        self._ready = None
        self.connection_count = 0

    def subprocess_env(self):
        s = self.settings
        env = {
            k: v
            for k, v in os.environ.items()
            if k
            in {
                "PATH",
                "HOME",
                "USER",
                "LOGNAME",
                "SHELL",
                "TMPDIR",
                "SYSTEMROOT",
                "HF_HOME",
                "HF_HUB_CACHE",
                "SENTENCE_TRANSFORMERS_HOME",
                "SSL_CERT_FILE",
            }
        }
        env.update(
            {
                "PYTHONPATH": str(PROJECT_ROOT),
                "CHROMA_DIR": str(s.chroma_dir.resolve()),
                "CASE_DB_PATH": str(s.case_db_path.resolve()),
                "EMBED_PROVIDER": s.embed_provider,
                "EMBED_MODEL_NAME": s.embed_model_name,
                "NEO4J_URI": s.neo4j_uri,
                "NEO4J_USER": s.neo4j_user,
                "NEO4J_PASSWORD": s.neo4j_password,
                "TEST_MODE": str(s.test_mode).lower(),
                "GENERATION_PROVIDER": s.generation_provider,
                "CHUNK_TOKENS": str(s.chunk_tokens),
                "CHUNK_OVERLAP": str(s.chunk_overlap),
                "WEB_MAX_BYTES": str(s.web_max_bytes),
                "MODEL_TIMEOUT_SEC": str(s.model_timeout_sec),
                "ANONYMIZED_TELEMETRY": "false",
            }
        )
        return env

    async def start(self):
        async with self._lock:
            if self._task is not None and not self._task.done():
                return
            self._queue = asyncio.Queue()
            self._ready = asyncio.get_running_loop().create_future()
            self._task = asyncio.create_task(self._own_session(), name="deskmate-mcp")
            try:
                await asyncio.wait_for(asyncio.shield(self._ready), timeout=60)
            except BaseException:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
                self._task = None
                raise

    async def _own_session(self):
        # AnyIO scopes are entered and exited by this same long-lived task.
        active = None
        try:
            params = StdioServerParameters(
                command=self.server_command,
                args=["-m", "backend.app.mcp.server"],
                cwd=self.cwd,
                env=self.subprocess_env(),
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(
                    read, write, read_timeout_seconds=timedelta(seconds=60)
                ) as session:
                    await session.initialize()
                    self.connection_count += 1
                    self._ready.set_result(None)
                    pending = set()

                    async def dispatch(name, arguments, future):
                        try:
                            if name == "__list_tools__":
                                result = await session.list_tools()
                                value = [t.model_dump() for t in result.tools]
                            else:
                                result = await session.call_tool(name, arguments)
                                if result.isError:
                                    message = " ".join(
                                        getattr(c, "text", "") for c in result.content
                                    )
                                    raise MCPToolError(f"MCP tool {name} failed: {message}")
                                value = self._coerce_tool_result(result)
                                if isinstance(value, dict) and value.get("status") == "error":
                                    raise MCPToolError(
                                        f'MCP tool {name} failed: {value.get("error")}'
                                    )
                            if not future.done():
                                future.set_result(value)
                        except MCPToolError as exc:
                            if not future.done():
                                future.set_exception(exc)
                        except BaseException:
                            if not future.done():
                                future.set_exception(
                                    MCPConnectionError("DeskMate MCP request failed")
                                )

                    try:
                        while True:
                            item = await self._queue.get()
                            if item is None:
                                break
                            task = asyncio.create_task(dispatch(*item))
                            pending.add(task)
                            task.add_done_callback(pending.discard)
                    finally:
                        if pending:
                            await asyncio.gather(*pending, return_exceptions=True)
        except BaseException as exc:
            error = MCPConnectionError("DeskMate MCP connection failed")
            if self._ready is not None and not self._ready.done():
                self._ready.set_exception(error)
            if active is not None and not active.done():
                active.set_exception(error)
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            while not self._queue.empty():
                item = self._queue.get_nowait()
                if item is not None and not item[2].done():
                    item[2].set_exception(MCPConnectionError("DeskMate MCP session closed"))

    async def call_tool(self, tool_name: str, params: dict[str, Any]):
        for attempt in range(2):
            try:
                await self.start()
                future = asyncio.get_running_loop().create_future()
                self._queue.put_nowait((tool_name, params, future))
                return await future
            except MCPConnectionError:
                await self.close()
                if attempt:
                    raise

    async def close(self):
        async with self._lock:
            if self._task is not None:
                self._queue.put_nowait(None)
                await asyncio.gather(self._task, return_exceptions=True)
                self._task = None

    async def list_tools(self):
        return await self.call_tool("__list_tools__", {})

    async def search_knowledge(self, question: str, mode: str = "HYBRID", top_k: int = 6):
        return await self.call_tool(
            "search_knowledge", {"question": question, "mode": mode, "top_k": top_k}
        )

    async def search_web(self, query: str, max_results: int = 5):
        return await self.call_tool("search_web", {"query": query, "max_results": max_results})

    async def fetch_web_page(self, url: str):
        payload = await self.call_tool("fetch_web_page", {"url": url})
        if not isinstance(payload, dict):
            raise MCPToolError("fetch_web_page must return an object")
        return payload

    @staticmethod
    def _coerce_tool_result(result):
        payload = getattr(result, "structuredContent", None)
        if payload is None:
            for item in getattr(result, "content", []):
                if hasattr(item, "text"):
                    try:
                        payload = json.loads(item.text)
                        break
                    except json.JSONDecodeError:
                        continue
        if isinstance(payload, dict):
            if "tool" in payload and "status" in payload:
                return payload
            return payload.get("result", payload.get("results", payload))
        if isinstance(payload, list):
            return payload
        raise ValueError("result must contain a JSON object or array")
