#!/usr/bin/env python3
"""Verify DeskMate's official Neo4j MCP integration without mutating Aura."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import SecretStr

from backend.app.core.config import Settings
from backend.app.mcp.client import MCPConnectionError, MCPToolError
from backend.app.mcp.multi import MultiMCPClient
from backend.app.mcp.neo4j_external import build_neo4j_stdio_env


class _EmptyBuiltin:
    _task = None
    connection_count = 0

    async def start(self): pass
    async def close(self): pass
    async def list_tools(self): return []


def _spec(command: str) -> dict:
    return {
        "name": "neo4j", "transport": "stdio", "command": command, "args": [],
        "environment": {}, "read_tools": ["get-schema", "read-cypher"],
        "workflows": ["knowledge", "service_desk"], "allowed_tenants": [],
    }


def _contains_ok(value) -> bool:
    if isinstance(value, dict):
        return value.get("ok") == 1 or any(_contains_ok(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_ok(item) for item in value)
    if isinstance(value, str):
        try:
            return _contains_ok(json.loads(value))
        except (json.JSONDecodeError, TypeError):
            return '"ok":1' in value.replace(" ", "")
    return False


def offline_test() -> None:
    settings = Settings(
        _env_file=None, test_mode=True, generation_provider="stub", embed_provider="stub",
        neo4j_uri="neo4j+s://example.databases.neo4j.io", neo4j_user="reader",
        neo4j_password=SecretStr("offline-only"), neo4j_database="neo4j",
    )
    parent = {"PATH": "/bin", "UNRELATED": "excluded"}
    child = build_neo4j_stdio_env(settings, parent)
    assert child["NEO4J_MCP_READ_ONLY"] == "true"
    assert child["NEO4J_MCP_TELEMETRY"] == "false"
    assert "UNRELATED" not in child and "NEO4J_MCP_PASSWORD" not in parent
    print("offline adapter verification: ok")


async def live_test() -> None:
    settings = Settings()
    configured = [item for item in settings.mcp_servers if item.get("name") == "neo4j"]
    if len(configured) != 1:
        raise SystemExit("Neo4j MCP is not registered exactly once in MCP_SERVERS")
    command = configured[0].get("command", "")
    if not Path(command).is_absolute() or not shutil.which(command):
        raise SystemExit("Configured Neo4j MCP executable is unavailable")

    # Distinguish invalid credentials from a provisioning/network delay without
    # ever rendering an exception that may contain connection details.
    from neo4j import GraphDatabase
    from neo4j.exceptions import AuthError

    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
        connection_timeout=10,
        max_transaction_retry_time=0,
    )
    try:
        driver.verify_connectivity()
    except AuthError:
        raise SystemExit("Aura authentication failed; update the private credential and retry") from None
    except Exception:
        pass  # The MCP transport owns the bounded provisioning/connectivity retry below.
    finally:
        driver.close()

    client = MultiMCPClient(settings, builtin=_EmptyBuiltin())
    try:
        delay = 2
        for attempt in range(8):
            tools = await client.list_tools()
            names = {item["name"] for item in tools}
            if client.integration_status.get("neo4j") == "connected":
                break
            if attempt == 7:
                raise MCPConnectionError("Neo4j MCP did not connect during bounded verification")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 45)

        expected = {"neo4j__get-schema", "neo4j__read-cypher"}
        if not expected <= names or "neo4j__write-cypher" in names:
            raise MCPToolError("Neo4j MCP exposed an unexpected tool set")
        descriptors = {item["name"]: item for item in tools}
        if any(descriptors[name].get("_deskmate_read_permitted") is not True for name in expected):
            raise MCPToolError("Neo4j MCP read annotations did not pass DeskMate policy")

        schema = await client.call_tool("neo4j__get-schema", {})
        one = await client.call_tool(
            "neo4j__read-cypher", {"query": "RETURN 1 AS ok", "params": {}}
        )
        count = await client.call_tool(
            "neo4j__read-cypher",
            {"query": "MATCH (n) RETURN count(n) AS node_count", "params": {}},
        )
        if not _contains_ok(one):
            raise MCPToolError("The read-only verification query did not return ok=1")
        print("server connected: yes")
        print("tools discovered: " + ", ".join(sorted(expected)))
        print("get-schema status: " + schema["status"])
        print("RETURN 1 status: " + one["status"] + " (ok=1)")
        print("node count status: " + count["status"])
        print("node count result: " + json.dumps(count["result"], default=str))
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="connect to configured Aura")
    args = parser.parse_args()
    if args.live:
        asyncio.run(live_test())
    else:
        offline_test()


if __name__ == "__main__":
    main()
