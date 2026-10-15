"""Real independent MCP stdio servers; explicit grants, schemas and unavailable integrations."""

import sys
from types import SimpleNamespace
import pytest
from backend.app.agent.tools import AgentToolHarness
from backend.app.mcp.client import MCPToolError
from backend.app.mcp.multi import MultiMCPClient, ServerSpec

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

@pytest.mark.parametrize('values',[{'name':'deskmate','command':'python'},{'name':'remote','transport':'streamable_http','url':'http://example.com'},{'name':'remote','transport':'streamable_http','url':'https://example.com/?token=secret'}])
def test_invalid_integration_configuration(values):
    with pytest.raises(ValueError): ServerSpec.model_validate(values)
