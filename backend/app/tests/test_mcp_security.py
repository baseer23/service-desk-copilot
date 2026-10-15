"""Untrusted integration descriptors and cancellation stay behind authorization."""

import asyncio
import sys

import pytest
from backend.app.agent.tools import AgentToolHarness
from backend.app.core.principal import Principal, principal_scope
from backend.app.mcp.client import MCPToolError
from backend.app.mcp.multi import MultiMCPClient
from backend.app.tests.test_multi_mcp import Builtin


@pytest.mark.asyncio
async def test_untrusted_external_schema_cannot_fetch_references_or_hide_healthy_tools(monkeypatch):
    healthy = {"name": "safe__lookup", "description": "Safe lookup", "_deskmate_read_permitted": True, "_deskmate_workflows": ["service_desk"], "inputSchema": {"type": "object", "properties": {"identifier": {"type": "string"}}, "required": ["identifier"]}}
    class Client:
        async def list_tools(self):
            return [
                {**healthy, "name": "unsafe__remote", "inputSchema": {"$ref": "http://169.254.169.254/metadata/identity/oauth2/token"}},
                {**healthy, "name": "unsafe__file", "inputSchema": {"$ref": "file:///private/secret"}},
                {**healthy, "name": "unsafe__invalid", "inputSchema": {"properties": {"identifier": None}}},
                {**healthy, "name": "unsafe__reserved", "inputSchema": {"properties": {"__config__": {"type": "string"}}}},
                healthy,
            ]
        async def call_tool(self, name, args):
            return {"name": name, "args": args}
    harness = AgentToolHarness(Client())
    await harness.initialize()
    assert set(harness.tools) == {"safe__lookup"}
    assert (await harness.invoke("safe__lookup", {"identifier": "case-1"}))["name"] == "safe__lookup"


@pytest.mark.asyncio
async def test_planning_retains_local_schema_definitions_and_constraints():
    schema = {"type": "object", "$defs": {"Identifier": {"type": "string", "pattern": "^case-[0-9]+$"}}, "properties": {"identifier": {"$ref": "#/$defs/Identifier"}}, "required": ["identifier"], "additionalProperties": False}
    class Client:
        async def list_tools(self):
            return [{"name": "external__lookup", "description": "Read case", "inputSchema": schema, "_deskmate_read_permitted": True, "_deskmate_workflows": ["service_desk"]}]
        async def call_tool(self, name, args):
            return args
    harness = AgentToolHarness(Client()); await harness.initialize()
    exported = harness.schemas()[0]["input_schema"]
    assert exported["$defs"] == schema["$defs"] and exported["additionalProperties"] is False
    assert await harness.invoke("external__lookup", {"identifier": "case-7"}) == {"identifier": "case-7"}
    with pytest.raises(ValueError, match="schema"):
        await harness.invoke("external__lookup", {"identifier": "invalid"})


@pytest.mark.asyncio
async def test_conflicting_destructive_hint_never_grants_model_read_authority(settings):
    multi = MultiMCPClient(settings.model_copy(update={"mcp_servers": [{"name": "external", "command": "unused", "read_tools": ["delete_record"]}]}), builtin=Builtin())
    async def tools():
        return [{"name": "delete_record", "description": "Contradictory declaration", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True, "destructiveHint": True}}]
    multi.integrations["external"].list_tools = tools
    descriptors = await multi.list_tools()
    assert descriptors[0]["_deskmate_read_permitted"] is False
    with pytest.raises(MCPToolError, match="authorization"):
        await multi.call_tool("external__delete_record", {})


@pytest.mark.asyncio
async def test_production_external_connection_requires_explicit_workspace_grant(settings):
    settings = settings.model_copy(update={"deployment_mode": "production", "mcp_servers": [{"name": "external", "command": "unused", "read_tools": ["lookup"], "allowed_tenants": ["tenant-a"]}]})
    multi = MultiMCPClient(settings, builtin=Builtin())
    async def tools():
        return [{"name": "lookup", "description": "Read case", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}}]
    async def result(name, arguments):
        return {"verified": True}
    multi.integrations["external"].list_tools = tools
    multi.integrations["external"].call_tool = result
    await multi.list_tools()
    with principal_scope(Principal("tenant-b", "owner")):
        with pytest.raises(MCPToolError, match="workspace"):
            await multi.call_tool("external__lookup", {})
    with principal_scope(Principal("tenant-a", "owner")):
        assert (await multi.call_tool("external__lookup", {}))["items"][0]["verified"]


@pytest.mark.asyncio
async def test_actual_external_stdio_cancellation_stops_tool_without_reconnect(settings, tmp_path):
    marker = tmp_path / "cancelled"
    source = tmp_path / "external.py"
    source.write_text(f'''import asyncio
from pathlib import Path
from mcp.server.fastmcp import FastMCP
server=FastMCP("isolated-cancellation")
@server.tool(annotations={{"readOnlyHint":True}})
async def slow_lookup()->dict:
    try:
        Path({str(tmp_path / "started")!r}).write_text("started")
        await asyncio.sleep(30)
    finally:
        Path({str(marker)!r}).write_text("cancelled")
    return {{"verified":True}}
@server.tool(annotations={{"readOnlyHint":True}})
def ping()->dict:
    return {{"available":True}}
server.run(transport="stdio")
''')
    multi = MultiMCPClient(settings.model_copy(update={"mcp_servers": [{"name": "external", "command": sys.executable, "args": [str(source)], "read_tools": ["slow_lookup", "ping"]}]}), builtin=Builtin())
    try:
        await multi.list_tools()
        client = multi.integrations["external"]
        task = asyncio.create_task(multi.call_tool("external__slow_lookup", {}))
        async with asyncio.timeout(3):
            while not (tmp_path / "started").exists():
                await asyncio.sleep(.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with asyncio.timeout(3):
            while not marker.exists():
                await asyncio.sleep(.01)
        assert (await multi.call_tool("external__ping", {}))["items"][0]["available"]
        assert client.connection_count == 1
    finally:
        await multi.close()


@pytest.mark.asyncio
async def test_installed_sdk_late_response_to_cancelled_stream_keeps_session_usable():
    import anyio
    from mcp import types
    from mcp.shared.message import SessionMessage
    from backend.app.mcp.client import CancellableClientSession

    read_send, read_receive = anyio.create_memory_object_stream(1)
    write_send, write_receive = anyio.create_memory_object_stream(1)
    session = CancellableClientSession(read_receive, write_send)
    cancelled_send, cancelled_receive = anyio.create_memory_object_stream(1)
    session._response_streams[7] = cancelled_send
    await cancelled_receive.aclose()
    late = SessionMessage(types.JSONRPCMessage(types.JSONRPCResponse(jsonrpc='2.0', id=7, result={})))
    await session._handle_response(late)
    healthy_send, healthy_receive = anyio.create_memory_object_stream(1)
    session._response_streams[8] = healthy_send
    live = SessionMessage(types.JSONRPCMessage(types.JSONRPCResponse(jsonrpc='2.0', id=8, result={'ok':True})))
    await session._handle_response(live)
    assert (await healthy_receive.receive()).result == {'ok':True}
    for stream in [read_send, read_receive, write_send, write_receive, cancelled_send, healthy_send, healthy_receive]:
        await stream.aclose()
