import asyncio
import json
import pytest
from backend.app.agent.activity import activity_scope, instrument
from backend.app.models.provider_routing import provider_scope


def test_http_stream_contains_real_node_events_and_final_response(make_client):
    client=make_client({'MAX_RESEARCH_ROUNDS':'0'})
    response=client.post('/ask',json={'summary':'VPN connection is failing for my device'},headers={'Accept':'text/event-stream'})
    assert response.status_code==200
    assert response.headers['content-type'].startswith('text/event-stream')
    events=[]
    for block in response.text.split('\n\n'):
        lines=block.splitlines()
        if len(lines)>1 and lines[0].startswith('event:'):
            events.append((lines[0][7:],json.loads(lines[1][6:])))
    activities=[value for kind,value in events if kind=='activity']
    assert any(value['stage']=='classify_intent' and value['status']=='completed' for value in activities)
    assert any(value['stage']=='discover_tools' for value in activities)
    assert any(value['stage'].startswith('tool:') for value in activities)
    assert len({value['sequence'] for value in activities})==len(activities)
    assert events[-1][0]=='result'
    assert not any('prompt' in value or 'chain_of_thought' in value for value in activities)


@pytest.mark.asyncio
async def test_node_cancellation_emits_status_and_propagates():
    events=[]
    async def wait(_): await asyncio.Event().wait()
    with activity_scope(events.append):
        task=asyncio.create_task(instrument('reason',wait)({}))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    assert [x['status'] for x in events]==['running','cancelled']


def test_disconnected_tool_is_not_evidence_of_a_system_fact(make_client):
    client=make_client({'MAX_RESEARCH_ROUNDS':'0'})
    state={'case_id':'isolated','persist_case':False}
    result={'status':'unavailable','items':[{'available':False,'reason':'Not connected'}], 'source_system':'local_fixture','timestamp':'2026-10-14T00:00:00Z','reliability':1}
    evidence=client.app.state.agent._evidence(state,'get_user_context',result)
    assert evidence[0]['source_type']=='tool_error'
    assert evidence[0]['reliability']==0
    assert evidence[0]['source_quality']=='unclassified'


@pytest.mark.asyncio
async def test_stream_disconnect_cancels_full_producer_queue_without_orphan(settings):
    from backend.app.agent.streaming import investigation_stream
    from backend.app.agent.activity import emit
    finished = asyncio.Event()
    async def operation():
        try:
            for index in range(1000):
                await emit('reason', 'running', step=index)
        finally:
            finished.set()
    response = investigation_stream(None, settings, operation)
    iterator = response.body_iterator
    assert 'event: activity' in await anext(iterator)
    await asyncio.wait_for(iterator.aclose(), 1)
    assert finished.is_set()


@pytest.mark.asyncio
async def test_stream_captures_owner_and_provider_before_http_context_exits(settings):
    from backend.app.agent.streaming import investigation_stream
    from backend.app.core.principal import Principal, principal_scope, current_principal
    from backend.app.models.provider_routing import selected_provider
    observed = []
    async def operation():
        observed.append((current_principal(settings).tenant_id, current_principal(settings).owner_id, selected_provider(settings)))
        return {'complete': True}
    with principal_scope(Principal('tenant-a', 'owner-a')), provider_scope('groq'):
        first = investigation_stream(None, settings, operation)
    with principal_scope(Principal('tenant-b', 'owner-b')), provider_scope('azure_openai'):
        second = investigation_stream(None, settings, operation)
    async def consume(response):
        return [item async for item in response.body_iterator]
    await asyncio.gather(consume(first), consume(second))
    assert set(observed) == {('tenant-a', 'owner-a', 'groq'), ('tenant-b', 'owner-b', 'azure_openai')}


def test_disconnected_sources_are_not_offered_to_the_model(make_client):
    client=make_client({'ENABLE_LOCAL_FIXTURES':'false','MAX_RESEARCH_ROUNDS':'0'})
    harness=client.app.state.agent.harness
    offered={item['name'] for item in harness.schemas('service_desk')}
    assert 'search_knowledge' in offered
    assert not offered & {'get_user_context','get_active_alerts','get_device_context','get_recent_changes','search_previous_tickets'}
    # Historical fixture APIs remain honest for explicit inspection; discovery
    # metadata never manufactures a connected service-desk integration.
    result=client.portal.call(harness.invoke,'get_user_context',{'user_name':'unconnected'})
    assert result['status']=='unavailable'
