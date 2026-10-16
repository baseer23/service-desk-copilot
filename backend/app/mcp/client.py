"""Async MCP transport with one task owning the complete stdio lifetime."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import deque
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class MCPToolError(RuntimeError):
    """Server tool failure; the connection remains usable."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class MCPConnectionError(RuntimeError):
    """Transport or initialization failed."""


class CancellableClientSession(ClientSession):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cancelled_requests = deque(maxlen=256)

    async def _handle_response(self, message):
        import anyio

        if self._normalize_request_id(message.message.root.id) in self._cancelled_requests:
            return
        try:
            await super()._handle_response(message)
        except (anyio.BrokenResourceError, anyio.ClosedResourceError):
            # The SDK pops a per-request stream before awaiting delivery. A
            # concurrent caller cancellation may close that stream in between.
            # This late reply must not close the shared session receive loop.
            return

    async def send_request(self, request, *args, **kwargs):
        # The installed MCP SDK allocates this ID synchronously at the start of
        # send_request but does not notify the server when its caller cancels.
        # Capture it before the first await; concurrent requests get distinct IDs.
        request_id = self._request_id
        try:
            return await super().send_request(request, *args, **kwargs)
        except asyncio.CancelledError:
            self._cancelled_requests.append(request_id)
            from mcp import types
            import anyio
            with anyio.CancelScope(shield=True):
                with suppress(Exception):
                    await asyncio.wait_for(self.send_notification(types.ClientNotification(
                        types.CancelledNotification(params=types.CancelledNotificationParams(
                            requestId=request_id, reason="Investigation cancelled by caller",
                        ))
                    )), 1)
            raise


class DeskMateMCPClient:
    def __init__(self, settings=None, *, server_command=None, cwd=None, stderr=None):
        from backend.app.core.config import get_settings

        self.settings = settings or get_settings()
        self.server_command = server_command or sys.executable
        self.cwd = cwd or str(PROJECT_ROOT)
        self.stderr = stderr
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
                "SSL_CERT_FILE",
                "AZURE_CLIENT_ID",
                "AZURE_TENANT_ID",
                "AZURE_CLIENT_SECRET",
                "AZURE_FEDERATED_TOKEN_FILE",
                "AZURE_CONFIG_DIR",
                "IDENTITY_ENDPOINT",
                "IDENTITY_HEADER",
                "MSI_ENDPOINT",
                "MSI_SECRET",
            }
        }
        env.update(
            {
                "PYTHONPATH": os.pathsep.join(
                    dict.fromkeys(
                        [
                            str(PROJECT_ROOT),
                            *filter(None, os.environ.get("PYTHONPATH", "").split(os.pathsep)),
                        ]
                    )
                ),
                "CHROMA_DIR": str(s.chroma_dir.resolve()),
                "CASE_DB_PATH": str(s.case_db_path.resolve()),
                "EMBED_PROVIDER": s.embed_provider,
                "EMBED_MODEL_NAME": s.embed_model_name,
                "NEO4J_URI": s.neo4j_uri,
                "NEO4J_USER": s.neo4j_user,
                "NEO4J_PASSWORD": s.neo4j_password.get_secret_value(),
                "TEST_MODE": str(s.test_mode).lower(),
                "GENERATION_PROVIDER": s.generation_provider,
                "CHUNK_TOKENS": str(s.chunk_tokens),
                "CHUNK_OVERLAP": str(s.chunk_overlap),
                "WEB_MAX_BYTES": str(s.web_max_bytes),
                "MODEL_TIMEOUT_SEC": str(s.model_timeout_sec),
                "ANONYMIZED_TELEMETRY": "false",
                "KNOWLEDGE_SCOPE": s.knowledge_scope,
                "RESEARCH_SOURCE_POLICY": s.research_source_policy,
                "RESEARCH_PREFERRED_DOMAINS": json.dumps(s.research_preferred_domains),
                "MAX_RESEARCH_ROUNDS": str(s.max_research_rounds),
                "ALLOW_URL_INGEST": str(s.allow_url_ingest).lower(),
                "DATA_PLATFORM": s.data_platform,
                "AUTH_CLIENT_ID": s.auth_client_id,
                "AUTH_ALLOWED_TENANTS": json.dumps(s.auth_allowed_tenants),
                "AUTH_OPERATOR_IDS": json.dumps(s.auth_operator_ids),
                "ENABLE_LOCAL_FIXTURES": str(s.enable_local_fixtures).lower(),
                "ENABLE_PUBLIC_WEB_TOOLS": str(s.enable_public_web_tools).lower(),
                "DEPLOYMENT_MODE": s.deployment_mode,
            }
        )
        # Forward all voice configuration explicitly; never log this environment.
        for name in type(s).model_fields:
            if name.startswith(("voice_", "azure_", "local_")):
                value = getattr(s, name)
                if hasattr(value, "get_secret_value"):
                    value = value.get_secret_value()
                elif isinstance(value, list):
                    value = json.dumps(value)
                elif isinstance(value, bool):
                    value = str(value).lower()
                if name == "azure_openai_api_key" and s.azure_use_entra_id and not value:
                    # The SDK treats an exported empty key differently from an absent
                    # key and fails before using its Entra token provider.
                    continue
                env[name.upper()] = str(value) if value is not None else ""
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

    @asynccontextmanager
    async def transport(self):
        params = StdioServerParameters(
            command=self.server_command, args=["-m", "backend.app.mcp.server"],
            cwd=self.cwd, env=self.subprocess_env(),
        )
        async with stdio_client(params, errlog=self.stderr or sys.stderr) as streams:
            yield streams

    def request_meta(self):
        from backend.app.core.principal import current_principal, sign_principal
        return {"deskmate_authority": sign_principal(self.settings, current_principal(self.settings))} if self.settings.voice_session_signing_key else None

    def safe_retry_tools(self):
        return {
            "__list_tools__", "get_local_storage_status", "get_case", "list_cases",
            "get_case_history", "get_document", "list_documents", "search_memory",
            "search_remembered_facts", "get_user_context", "get_company_context",
            "get_site_context", "get_device_context", "get_active_alerts",
            "get_recent_changes", "list_available_context", "search_previous_tickets",
            "list_approved_domains", "resolve_domain", "reload_approved_domains",
        }

    async def _own_session(self):
        # AnyIO scopes are entered and exited by this same long-lived task.
        active = None
        try:
            async with self.transport() as (read, write):
                async with CancellableClientSession(
                    read,
                    write,
                    read_timeout_seconds=timedelta(
                        seconds=180 if self.settings.uses_record_store else 60
                    ),
                ) as session:
                    await session.initialize()
                    self.connection_count += 1
                    self._ready.set_result(None)
                    pending = set()

                    async def dispatch(name, arguments, future, meta):
                        try:
                            if name == "__list_tools__":
                                result = await session.list_tools()
                                value = [t.model_dump() for t in result.tools]
                            else:
                                result = await session.call_tool(name, arguments, meta=meta)
                                if result.isError:
                                    message = " ".join(
                                        getattr(c, "text", "") for c in result.content
                                    )
                                    raise MCPToolError(f"MCP tool {name} failed: {message}")
                                value = self._coerce_tool_result(result)
                                if isinstance(value, dict) and value.get("status") == "error":
                                    raise MCPToolError(
                                        f'MCP tool {name} failed: {value.get("error")}',
                                        (value.get("result") or {}).get("http_status", 502),
                                    )
                            if not future.done():
                                future.set_result(value)
                        except asyncio.CancelledError:
                            if not future.done():
                                future.cancel()
                            raise
                        except MCPToolError as exc:
                            if not future.done():
                                future.set_exception(exc)
                        except Exception:
                            if not future.done():
                                future.set_exception(
                                    MCPConnectionError("DeskMate MCP request failed")
                                )

                    try:
                        while True:
                            item = await self._queue.get()
                            if item is None:
                                break
                            if item[2].cancelled():
                                continue  # Never dispatch an abandoned queued write/read.
                            task = asyncio.create_task(dispatch(*item))
                            future = item[2]
                            def cancel_dispatch(completed, dispatch_task=task):
                                if completed.cancelled():
                                    dispatch_task.cancel()
                            future.add_done_callback(cancel_dispatch)
                            def finished(completed, response=future, callback=cancel_dispatch):
                                pending.discard(completed)
                                response.remove_done_callback(callback)
                            pending.add(task)
                            task.add_done_callback(finished)
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
                meta = self.request_meta()
                self._queue.put_nowait((tool_name, params, future, meta))
                return await future
            except MCPConnectionError:
                await self.close()
                # Do not repeat a write after an ambiguous transport failure.
                if attempt or tool_name not in self.safe_retry_tools():
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
