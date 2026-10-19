import asyncio
from types import SimpleNamespace
import pytest
from backend.app.mcp.client import DeskMateMCPClient, MCPToolError
from backend.app.agent.models import MemoryFact
from datetime import datetime, timezone


def test_public_research_tools_are_available_to_knowledge_agent():
    from backend.app.agent.tools import KNOWLEDGE_TOOLS, READ_TOOLS

    for tool in ("search_news", "get_current_time"):
        assert tool in KNOWLEDGE_TOOLS
        assert tool in READ_TOOLS


def test_hosted_mcp_subprocess_keeps_installed_library_path(settings, monkeypatch, tmp_path):
    import subprocess
    import sys

    library = tmp_path / "host-installed-libraries"
    library.mkdir()
    (library / "hosted_dependency.py").write_text("VALUE = 'available'\n")
    monkeypatch.setenv("PYTHONPATH", str(library))
    client = DeskMateMCPClient(settings)
    process = subprocess.run(
        [sys.executable, "-c", "import hosted_dependency; print(hosted_dependency.VALUE)"],
        env=client.subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0 and process.stdout.strip() == "available"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "save_document",
        "index_document",
        "ingest_text",
        "save_case",
        "save_fact",
        "search_knowledge",
    ],
)
async def test_ambiguous_write_or_paid_retrieval_transport_failure_is_not_replayed(
    settings, monkeypatch, name
):
    from backend.app.mcp.client import MCPConnectionError

    client = DeskMateMCPClient(settings)
    starts = []
    async def unavailable():
        starts.append(1)
        raise MCPConnectionError("Explicit isolated transport failure")
    async def close():
        pass
    monkeypatch.setattr(client, "start", unavailable)
    monkeypatch.setattr(client, "close", close)
    with pytest.raises(MCPConnectionError):
        await client.call_tool(name, {})
    assert starts == [1]


@pytest.mark.asyncio
async def test_ten_calls_errors_reuse_and_clean_close(settings):
    client = DeskMateMCPClient(settings)
    await client.start()
    for _ in range(10):
        result = await client.call_tool("get_user_context", {"user_name": "Jake Peralta"})
        assert result["source_system"] == "local_fixture"
    assert client.connection_count == 1
    with pytest.raises(MCPToolError, match="missing_tool"):
        await client.call_tool("missing_tool", {})
    with pytest.raises(MCPToolError, match="mode"):
        await client.search_knowledge("VPN", mode="INVALID")
    assert (await client.call_tool("get_company_context", {"company": "B99"}))["result"]["name"] == "B99"
    await client.close()
    assert client._task is None
    await client.close()


@pytest.mark.asyncio
async def test_fixture_tools_and_shared_memory(settings):
    from backend.app.agent.memory import CaseMemoryStore

    client = DeskMateMCPClient(settings)
    try:
        await client.start()
        inputs = {
            "search_previous_tickets": {
                "query": "VPN authentication",
                "user": "Jake Peralta",
                "site": "2nd floor",
            },
            "get_user_context": {"user_name": "Jake Peralta"},
            "get_company_context": {"company": "B99"},
            "get_site_context": {"company": "B99", "site": "2nd floor"},
            "get_device_context": {"device_or_user": "Jake Peralta"},
            "get_active_alerts": {"company": "B99", "site": "2nd floor"},
            "get_recent_changes": {"company": "B99", "site": "2nd floor"},
        }
        for name, arguments in inputs.items():
            result = await client.call_tool(name, arguments)
            rows = result["items"]
            assert rows
            assert all(r["source_system"] == "local_fixture" for r in rows)
        user = await client.call_tool("get_user_context", inputs["get_user_context"])
        assert user["result"]["current_site"] is None and user["result"]["required_update_installed"] is None
        alert = (await client.call_tool("get_active_alerts", inputs["get_active_alerts"]))["items"][0]
        assert alert["status"] == "active" and alert["technician_event"]
        fact = MemoryFact(
            subject="Jake Peralta",
            predicate="current_site",
            value="B99 2nd floor",
            source="human_confirmed",
            source_system="human_input",
            observed_at=datetime.now(timezone.utc).isoformat(),
            confidence=1,
            case_id="unique-test",
        )
        await client.call_tool("remember_fact", {"fact": fact.model_dump()})
        await client.call_tool(
            "save_case_note",
            {"case_id": "unique-test", "text": "A real MCP note", "source": "human_confirmed"},
        )
        found = await client.call_tool("search_remembered_facts", {"subject": "Jake Peralta"})
        assert found["items"][0]["case_id"] == "unique-test"
        parent = CaseMemoryStore(settings.case_db_path)
        assert parent.search_facts("Jake Peralta")[0]["value"] == "B99 2nd floor"
        assert parent.list_events("unique-test")[0]["content"] == "A real MCP note"
        with pytest.raises(MCPToolError):
            await client.call_tool(
                "remember_fact", {"fact": {**fact.model_dump(), "source": "llm_guess"}}
            )
    finally:
        await client.close()


def test_configuration_is_explicit_and_fetch_dict_is_preserved(settings):
    client = DeskMateMCPClient(settings)
    env = client.subprocess_env()
    for key in (
        "CHROMA_DIR",
        "EMBED_PROVIDER",
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASSWORD",
        "CASE_DB_PATH",
    ):
        assert key in env
    assert env["CASE_DB_PATH"] == str(settings.case_db_path.resolve())
    data = {"url": "https://example.com", "title": "Example", "text": "Useful text"}
    assert client._coerce_tool_result(SimpleNamespace(structuredContent={"result": data})) == data


@pytest.mark.asyncio
async def test_reconnect_after_transport_owner_stops(settings):
    client = DeskMateMCPClient(settings)
    try:
        await client.start()
        client._task.cancel()
        await asyncio.gather(client._task, return_exceptions=True)
        result = await client.call_tool("get_company_context", {"company": "B99"})
        assert result["result"]["name"] == "B99"
        assert client.connection_count == 2
    finally:
        await client.close()
