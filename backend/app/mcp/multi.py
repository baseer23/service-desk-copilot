"""Explicitly configured MCP integrations; discovery never grants write authority."""

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from urllib.parse import urlsplit

from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.mcp.schema import checked_schema
from backend.app.mcp.client import DeskMateMCPClient, MCPConnectionError, MCPToolError
from backend.app.mcp.neo4j_external import build_neo4j_stdio_env


class ServerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,30}$")
    transport: str = "stdio"
    command: str = ""
    args: list[str] = []
    url: str = ""
    environment: dict[str, str] = {}  # child name → existing environment variable name
    bearer_token_env: str = ""
    read_tools: list[str] = []
    workflows: list[str] = ["service_desk"]
    allowed_tenants: list[str] = []

    @model_validator(mode="after")
    def validate_connection(self):
        if self.name == "deskmate" or "__" in self.name:
            raise ValueError("Reserved MCP server name")
        if self.transport == "stdio":
            if not self.command:
                raise ValueError("An explicit executable is required")
        elif self.transport == "streamable_http":
            parsed = urlsplit(self.url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Remote MCP requires a credential-free HTTPS URL")
            if parsed.query or parsed.fragment:
                raise ValueError("Remote MCP credentials belong in private environment variables")
        else:
            raise ValueError("Unsupported MCP transport")
        if any(value not in {"service_desk", "knowledge"} for value in self.workflows):
            raise ValueError("Unknown MCP workflow grant")
        for value in [*self.environment, *self.environment.values(), self.bearer_token_env]:
            if value and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
                raise ValueError("Invalid credential environment reference")
        return self


class ExternalMCPClient(DeskMateMCPClient):
    def __init__(self, settings, spec):
        super().__init__(settings)
        self.spec = spec

    def request_meta(self):
        return None  # Never export DeskMate's signed internal authority to other servers.

    def safe_retry_tools(self):
        return {"__list_tools__"}

    @asynccontextmanager
    async def transport(self):
        if self.spec.transport == "stdio":
            if self.spec.name == "neo4j":
                environment = build_neo4j_stdio_env(self.settings, os.environ)
            else:
                environment = get_default_environment()
                for child_key, source_key in self.spec.environment.items():
                    value = os.environ.get(source_key)
                    if value is None:
                        raise MCPConnectionError("An integration credential is missing")
                    environment[child_key] = value
            params = StdioServerParameters(command=self.spec.command, args=self.spec.args,
                                           env=environment)
            # SDK/process diagnostics may contain third-party request bodies.
            with open(os.devnull, "w") as log:
                async with stdio_client(params, errlog=log) as streams:
                    yield streams
        else:
            import httpx
            from mcp.client.streamable_http import streamable_http_client

            headers = {}
            if self.spec.bearer_token_env:
                token = os.environ.get(self.spec.bearer_token_env)
                if not token:
                    raise MCPConnectionError("An integration credential is missing")
                headers["Authorization"] = "Bearer " + token
            async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=False) as http:
                async with streamable_http_client(self.spec.url, http_client=http) as streams:
                    yield streams[0], streams[1]

    @staticmethod
    def _coerce_tool_result(result):
        content = getattr(result, "structuredContent", None)
        if content is None:
            texts = [item.text for item in result.content if getattr(item, "type", "") == "text"]
            content = []
            for value in texts:
                try:
                    parsed = json.loads(value)
                    content.extend(parsed if isinstance(parsed, list) else [parsed])
                except json.JSONDecodeError:
                    content.append({"text": value})
        return content


class MultiMCPClient:
    def __init__(self, settings, builtin=None):
        self.settings = settings
        self.builtin = builtin or DeskMateMCPClient(settings)
        specs = [ServerSpec.model_validate(value) for value in settings.mcp_servers]
        if len({spec.name for spec in specs}) != len(specs):
            raise ValueError("MCP server names must be unique")
        self.integrations = {spec.name: ExternalMCPClient(settings, spec) for spec in specs}
        self.discovered = {}
        self.integration_status = {}

    @property
    def _task(self):
        return self.builtin._task

    @property
    def connection_count(self):
        return self.builtin.connection_count

    async def start(self):
        await self.builtin.start()

    async def close(self):
        await asyncio.gather(self.builtin.close(), *(c.close() for c in self.integrations.values()))

    async def list_tools(self):
        tools = list(await self.builtin.list_tools())
        self.discovered = {}
        async def discover(name, client):
            try:
                items = await asyncio.wait_for(client.list_tools(), 15)
                self.integration_status[name] = "connected"
                return [(name, item) for item in items]
            except (MCPConnectionError, asyncio.TimeoutError):
                self.integration_status[name] = "unavailable"
                return []
        groups = await asyncio.gather(*(discover(n, c) for n, c in self.integrations.items()))
        for group in groups:
            for server, item in group:
                original = item["name"]
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", original):
                    continue
                try:
                    checked_schema(item["inputSchema"])
                except (KeyError, TypeError, ValueError):
                    continue
                annotations = item.get("annotations") or {}
                spec = self.integrations[server].spec
                permitted = (
                    original in spec.read_tools and annotations.get("readOnlyHint") is True
                    and annotations.get("destructiveHint") is not True
                )
                public = server + "__" + original
                self.discovered[public] = (server, original, permitted)
                tools.append({**item, "name": public, "_deskmate_read_permitted": permitted,
                              "_deskmate_workflows": spec.workflows})
        return tools

    async def call_tool(self, name, arguments):
        if "__" not in name:
            return await self.builtin.call_tool(name, arguments)
        route = self.discovered.get(name)
        if not route or not route[2]:
            raise MCPToolError("This integration action requires explicit operator authorization", 403)
        server, original, _ = route
        if self.settings.deployment_mode == "production":
            from backend.app.core.principal import current_principal
            if current_principal(self.settings).tenant_id not in self.integrations[server].spec.allowed_tenants:
                raise MCPToolError("This integration is not authorized for the current workspace", 403)
        try:
            output = await asyncio.wait_for(self.integrations[server].call_tool(original, arguments), 30)
        except (MCPConnectionError, MCPToolError, asyncio.TimeoutError):
            raise MCPToolError("The connected integration is temporarily unavailable") from None
        if server == "neo4j" and original == "read-cypher":
            raw = output
            envelope = output
            while isinstance(envelope, dict) and any(
                key in envelope for key in ("result", "results")
            ):
                nested = envelope.get("rows")
                if isinstance(nested, list):
                    break
                envelope = envelope.get("result", envelope.get("results"))
            if isinstance(envelope, dict) and isinstance(envelope.get("rows"), list):
                rows = envelope["rows"]
            elif isinstance(envelope, list) and all(isinstance(row, dict) for row in envelope):
                rows = envelope
            elif isinstance(envelope, dict) and not any(
                key in envelope for key in ("columns", "data", "records", "rows")
            ):
                rows = [envelope]
            else:
                raise MCPToolError(
                    "Neo4j read-cypher returned an unsupported response envelope",
                    code="NEO4J_MCP_RESPONSE_SHAPE",
                )
            if any(not isinstance(row, dict) for row in rows):
                raise MCPToolError(
                    "Neo4j read-cypher rows must be objects",
                    code="NEO4J_MCP_RESPONSE_SHAPE",
                )
            metadata = {
                key: value for key, value in (raw.items() if isinstance(raw, dict) else [])
                if key in {"truncated", "has_more", "warnings", "error"}
            }
            return {
                "tool": name,
                "status": "ok",
                "items": rows,
                "result": raw,
                "metadata": metadata,
                "timestamp": datetime.now(UTC).isoformat(),
                "source_system": "mcp:" + server,
                "reliability": 0.8,
            }
        values = output if isinstance(output, list) else [output]
        items = [value if isinstance(value, dict) else {"text": str(value)} for value in values]
        return {"tool": name, "status": "ok", "items": items, "result": output,
                "timestamp": datetime.now(UTC).isoformat(), "source_system": "mcp:" + server,
                "reliability": 0.8}
