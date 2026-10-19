"""Real independent MCP stdio servers; explicit grants, schemas and unavailable integrations."""

import sys
from types import SimpleNamespace
import pytest
from backend.app.agent.tools import AgentToolHarness
from backend.app.agent.models import InvestigationPlan
from backend.app.mcp.client import MCPConnectionError, MCPToolError
from backend.app.mcp.multi import MultiMCPClient, ServerSpec
from backend.app.mcp.neo4j_external import build_neo4j_stdio_env
from pydantic import SecretStr

class Builtin:
    _task = None
    connection_count = 0
    async def start(self): pass
    async def close(self): pass
    async def list_tools(self): return []

@pytest.mark.asyncio
async def test_two_actual_servers_discovered_and_only_granted_reads_invoked(settings, tmp_path):
    source = tmp_path/'external.py'
    source.write_text('''from mcp.server.fastmcp import FastMCP
server=FastMCP("isolated-external")
@server.tool(annotations={"readOnlyHint":True})
def lookup(identifier: str)->dict:
    return {"id":identifier,"verified":True}
@server.tool(annotations={"readOnlyHint":False,"destructiveHint":True})
def delete_record(identifier:str)->dict:
    raise RuntimeError("This must never execute")
server.run(transport="stdio")
''')
    def spec(name):
        return {"name":name,"command":sys.executable,"args":[str(source)],"read_tools":["lookup","delete_record"],"workflows":["service_desk"]}
    multi=MultiMCPClient(settings.model_copy(update={"mcp_servers":[spec('first'),spec('second')]}), builtin=Builtin())
    try:
        harness=AgentToolHarness(multi)
        await harness.initialize()
        assert {'first__lookup','second__lookup'} <= harness.allowed_names('service_desk')
        assert 'first__delete_record' not in harness.allowed_names('service_desk')
        assert not harness.allowed_names('knowledge') & {'first__lookup','second__lookup'}
        result=await harness.invoke('first__lookup',{'identifier':'fictional-123'})
        assert result['items'][0]['verified'] is True
        assert result['source_system']=='mcp:first'
        assert multi.integrations['first'].request_meta() is None
        with pytest.raises(MCPToolError, match='authorization'):
            await multi.call_tool('first__delete_record',{'identifier':'anything'})
        assert all(status=='connected' for status in multi.integration_status.values())
    finally:
        await multi.close()

@pytest.mark.asyncio
async def test_unavailable_server_does_not_hide_builtin_capabilities(settings):
    multi=MultiMCPClient(settings.model_copy(update={'mcp_servers':[{'name':'missing','command':'/no/such/executable'}]}),builtin=Builtin())
    try:
        assert await multi.list_tools()==[]
        assert multi.integration_status=={'missing':'unavailable'}
    finally: await multi.close()

@pytest.mark.asyncio
async def test_full_discovered_schema_rejects_nested_invalid_arguments():
    class Client:
        async def list_tools(self):
            return [{'name':'support__lookup','description':'Read account info','_deskmate_read_permitted':True,'_deskmate_workflows':['service_desk'],'inputSchema':{'type':'object','properties':{'record':{'type':'object','properties':{'id':{'type':'string','pattern':'^case-[0-9]+$'}},'required':['id'],'additionalProperties':False}},'required':['record'],'additionalProperties':False}}]
        async def call_tool(self,*args): raise AssertionError('Invalid data reached MCP')
    harness=AgentToolHarness(Client());await harness.initialize()
    call=SimpleNamespace(name='support__lookup',arguments={'record':{'id':'wrong'}})
    with pytest.raises(ValueError,match='schema'): harness.validate_plan(SimpleNamespace(tool_calls=[call]),'service_desk')
    with pytest.raises(ValueError,match='schema'): await harness.invoke(call.name,call.arguments)


@pytest.mark.asyncio
async def test_live_database_plan_invokes_schema_without_document_search():
    calls = []
    class Client:
        async def list_tools(self):
            common = {'description':'read','_deskmate_read_permitted':True,
                      '_deskmate_workflows':['knowledge'],
                      'inputSchema':{'type':'object','properties':{},
                                     'additionalProperties':False}}
            return [{**common,'name':'neo4j__get-schema'},
                    {**common,'name':'search_knowledge'}]
        async def call_tool(self,name,arguments):
            calls.append((name,arguments))
            return {'tool':name,'status':'ok','items':[{'labels':[]}]}
    harness=AgentToolHarness(Client());await harness.initialize()
    plan=InvestigationPlan(
        goal='Determine whether the connected infrastructure database is empty',
        tool_calls=[{'name':'neo4j__get-schema','arguments':{},
                     'reason':'Inspect current database structure'}],
        skipped_sources=[{'source':'uploaded documents',
                          'reason':'Documents cannot establish live database contents'}],
        parallel_groups=[[0]],stop_condition='Current graph structure is observed')
    harness.validate_plan(plan,'knowledge')
    for request in plan.tool_calls:
        await harness.invoke(request.name,request.arguments)
    assert calls==[('neo4j__get-schema',{})]
    assert sum(name=='search_knowledge' for name,_ in calls)==0

@pytest.mark.parametrize('values',[{'name':'deskmate','command':'python'},{'name':'remote','transport':'streamable_http','url':'http://example.com'},{'name':'remote','transport':'streamable_http','url':'https://example.com/?token=secret'}])
def test_invalid_integration_configuration(values):
    with pytest.raises(ValueError): ServerSpec.model_validate(values)


def test_neo4j_environment_is_canonical_isolated_and_read_only(settings):
    configured = settings.model_copy(update={
        "neo4j_uri": "neo4j+s://example.databases.neo4j.io",
        "neo4j_user": "reader",
        "neo4j_password": SecretStr("private-test-value"),
        "neo4j_database": "support",
    })
    parent = {"PATH": "/bin", "UNRELATED_SECRET": "must-not-pass"}
    child = build_neo4j_stdio_env(configured, parent)
    assert child == {
        "PATH": "/bin",
        "NEO4J_MCP_URI": "neo4j+s://example.databases.neo4j.io",
        "NEO4J_MCP_USERNAME": "reader",
        "NEO4J_MCP_PASSWORD": "private-test-value",
        "NEO4J_MCP_DATABASE": "support",
        "NEO4J_MCP_READ_ONLY": "true",
        "NEO4J_MCP_TELEMETRY": "false",
    }
    assert parent == {"PATH": "/bin", "UNRELATED_SECRET": "must-not-pass"}


def test_neo4j_environment_missing_credentials_has_sanitized_error(settings):
    with pytest.raises(MCPConnectionError) as caught:
        build_neo4j_stdio_env(settings, {})
    assert "missing" in str(caught.value).lower()
    assert "password" not in str(caught.value).lower()
