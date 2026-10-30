import asyncio
import json
import pytest
from backend.app.agent.activity import activity_scope, instrument
from backend.app.agent.models import InvestigationPlan, RequestIntent
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
    stages = [value['stage'] for value in activities]
    assert stages.index('understanding_summary') < stages.index('research_plan')
    assert stages.index('research_plan') < stages.index('evidence_evaluation')
    assert next(index for index, stage in enumerate(stages) if stage.startswith('tool:')) < stages.index('evidence_evaluation')
    assert len({value['sequence'] for value in activities})==len(activities)
    assert events[-1][0]=='result'
    assert not any('prompt' in value or 'chain_of_thought' in value for value in activities)


def test_follow_up_event_contains_the_real_question_before_result(make_client):
    client = make_client({'MAX_RESEARCH_ROUNDS': '0'})
    response = client.post(
        '/ask', json={'summary': 'my vpn isnt working'},
        headers={'Accept': 'text/event-stream'},
    )
    blocks = [block for block in response.text.split('\n\n') if block]
    events = []
    for block in blocks:
        lines = block.splitlines()
        name = lines[0][7:]
        data = json.loads(lines[1][6:])
        events.append((name, data))
    follow_up = next(data for name, data in events if name == 'activity' and data['stage'] == 'follow_up')
    assert follow_up['question'] == 'What exact error message or code appears when the VPN connection fails?'
    assert events.index(('activity', follow_up)) < next(
        index for index, (name, _) in enumerate(events) if name == 'result'
    )


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


def test_plan_streams_objective_and_agent_generated_research_questions(make_client):
    client = make_client({'MAX_RESEARCH_ROUNDS': '0'})
    agent = client.app.state.agent

    async def planned(stage, context, schema):
        assert stage == 'plan'
        return InvestigationPlan(
            goal='Assess the documented eligibility requirements',
            known_facts=['The occupation is support technician'],
            unknowns=['Which Nova Scotia stream applies?', 'Does the job offer meet the stream rules?'],
            tool_calls=[{
                'name': 'search_knowledge',
                'arguments': {'question': 'Nova Scotia graduate stream eligibility'},
                'reason': 'Check existing authoritative workspace material first',
            }],
        skipped_sources=[{
            'source': 'live infrastructure',
            'reason': 'The request asks only about uploaded policy material',
        }],
            stop_condition='Official requirements and applicant-specific gaps are identified',
        )

    agent.provider.generate_structured = planned
    events = []
    state = {
        'case_id': 'visible-plan', 'persist_case': False, 'workflow': 'knowledge',
        'ticket': {'summary': 'Am I eligible?'}, 'evidence': [],
    }
    async def run():
        with activity_scope(events.append):
            return await agent.plan(state)

    update = client.portal.call(run)

    assert update['research_questions'] == [
        'Which Nova Scotia stream applies?', 'Does the job offer meet the stream rules?'
    ]
    event = next(row for row in events if row['stage'] == 'research_plan')
    assert event['objective'] == 'Assess the documented eligibility requirements'
    assert event['research_questions'] == update['research_questions']
    decision = next(row for row in events if row['stage'] == 'source_decision')
    assert decision['actions'][0]['tool'] == 'search_knowledge'
    assert decision['skipped_sources'] == [{
        'source': 'live infrastructure',
        'reason': 'The request asks only about uploaded policy material',
    }]


def test_empty_evidence_plan_is_retried_with_validation_feedback(make_client):
    client = make_client({'MAX_RESEARCH_ROUNDS': '0'})
    agent = client.app.state.agent
    contexts = []

    async def planned(stage, context, schema):
        assert stage == 'plan'
        contexts.append(context.copy())
        if len(contexts) == 1:
            return InvestigationPlan(
                goal='Find the requested workspace requirements',
                additional_evidence_useful=True,
                unknowns=['The applicable documented requirements'],
                stop_condition='The requirements are supported by a document passage',
            )
        return InvestigationPlan(
            goal='Find the requested workspace requirements',
            additional_evidence_useful=True,
            unknowns=['The applicable documented requirements'],
            tool_calls=[{
                'name': 'search_knowledge',
                'arguments': {'question': 'NSNP requirements'},
                'reason': 'The user explicitly requested stored knowledge',
            }],
            stop_condition='The requirements are supported by a document passage',
        )

    agent.provider.generate_structured = planned
    update = client.portal.call(agent.plan, {
        'case_id': 'empty-plan-retry', 'persist_case': False,
        'workflow': 'knowledge', 'ticket': {'summary': 'From knowledge tell me NSNP requirements'},
        'intent': {'knowledge_source': 'documents'}, 'evidence': [],
    })

    assert len(contexts) == 2
    assert 'requests no tool' in contexts[1]['plan_validation_feedback']['message']
    assert update['plan']['tool_calls'][0]['name'] == 'search_knowledge'


@pytest.mark.parametrize('request_text', [
    'From knowledge tell me what are the requirements of NSNP.',
    'Check knowledge for the NSNP requirements.',
    'According to my uploaded PDF, who was injured?',
])
def test_explicit_local_source_scope_overrides_general_classifier(make_client, request_text):
    client = make_client()
    agent = client.app.state.agent

    async def classified(stage, context, schema):
        assert stage == 'classify_intent'
        return RequestIntent(
            workflow='knowledge', knowledge_source='general', goal='Answer the request',
            justification='Incorrectly treated as model knowledge',
        )

    agent.provider.generate_structured = classified
    update = client.portal.call(agent.classify_intent, {
        'case_id': 'source-scope', 'persist_case': False,
        'ticket': {'summary': request_text}, 'chat_messages': [],
    })
    assert update['intent']['knowledge_source'] == 'documents'
    assert update['explicit_saved_knowledge'] is True


def test_structured_document_intent_marks_title_question_as_saved_knowledge(make_client):
    client = make_client()
    agent = client.app.state.agent

    async def classified(stage, context, schema):
        return RequestIntent(
            workflow='knowledge', knowledge_source='documents',
            goal='Answer from the uploaded document',
            justification='The title identifies a saved document',
        )

    agent.provider.generate_structured = classified
    update = client.portal.call(agent.classify_intent, {
        'case_id': 'document-title-scope', 'persist_case': False,
        'ticket': {'summary': 'What does NSNP 200 say about eligibility?'},
        'chat_messages': [],
    })
    assert update['explicit_saved_knowledge'] is True
    assert update['intent']['knowledge_source'] == 'documents'


def test_verifier_gap_routes_to_bounded_replanning(make_client):
    agent = make_client({'MAX_INVESTIGATION_CYCLES': '2'}).app.state.agent
    state = {
        'research_iteration': 1,
        'decision': {'question_for_human': None},
        'verification': {'should_continue_research': True},
    }
    assert agent.verification_route(state) == 'plan'
    state['research_iteration'] = 2
    assert agent.verification_route(state) == 'finalize'


def test_from_knowledge_request_executes_local_retrieval(make_client):
    client = make_client({'MAX_RESEARCH_ROUNDS': '0'})
    client.post('/ingest/paste', json={
        'title': 'NSNP eligibility guide',
        'text': (
            'Nova Scotia Nominee Program skilled worker requirements include a full-time '
            'permanent job offer from a Nova Scotia employer, one year of related work '
            'experience, and the required language proficiency for the occupation.'
        ),
    })
    calls = []
    original = client.app.state.agent.harness.invoke

    async def record(name, arguments):
        calls.append(name)
        return await original(name, arguments)

    client.app.state.agent.harness.invoke = record
    body = client.post('/ask', json={
        'summary': 'From knowledge tell me what are the requirements of NSNP.'
    }).json()

    assert 'search_knowledge' in calls
    assert 'search_graph' in calls
    assert any(event.get('tool') == 'search_knowledge' for event in body['events'])


def test_saved_knowledge_graph_stage_prefers_official_read_only_mcp(make_client, monkeypatch):
    client = make_client()
    agent = client.app.state.agent
    captured = []
    monkeypatch.setattr(
        agent.harness, 'allowed_names',
        lambda workflow: {'neo4j__read-cypher'} if workflow == 'knowledge' else set(),
    )

    async def graph_call(state, name, arguments):
        captured.append((name, arguments))
        return {
            'tool': name, 'status': 'ok', 'items': [],
            'source_system': 'mcp:neo4j', 'reliability': 0.8,
        }

    monkeypatch.setattr(agent, '_call', graph_call)
    result = client.portal.call(agent._mandatory_saved_knowledge_graph_read, {
        'case_id': 'graph-stage', 'persist_case': False,
        'ticket': {'summary': 'From knowledge, summarize the uploaded VPN guide.'},
    })
    name, arguments = captured[0]
    assert name == 'neo4j__read-cypher'
    assert '$tenant_id' in arguments['query'] and '$terms' in arguments['query']
    assert arguments['params']['tenant_id'] == 'local-demo'
    assert result['output']['source_system'] == 'mcp:neo4j'


def test_tool_completion_streams_concise_observations(make_client):
    client = make_client({'MAX_RESEARCH_ROUNDS': '0'})
    agent = client.app.state.agent
    original = agent.harness.invoke

    async def invoke(name, arguments):
        if name == 'search_knowledge':
            return {
                'items': [{
                    'title': 'Graduate stream policy', 'text': 'Eligibility requirements',
                    'metadata': {
                        'title': 'Graduate stream policy', 'page': 4,
                        'source': '/documents/abc/download',
                        'extraction_method': 'document_intelligence',
                    },
                }],
                'source_system': 'local_knowledge', 'timestamp': '2026-10-14T00:00:00Z',
                'reliability': 0.9,
            }
        return await original(name, arguments)

    agent.harness.invoke = invoke
    events = []
    async def run():
        with activity_scope(events.append):
            await agent._call(
                {'case_id': 'visible-tool', 'persist_case': False},
                'search_knowledge', {'question': 'graduate stream eligibility'},
            )

    client.portal.call(run)
    event = next(
        row for row in events
        if row['stage'] == 'tool:search_knowledge' and row['status'] == 'completed'
    )
    assert event['query'] == 'graduate stream eligibility'
    assert event['findings'] == ['Graduate stream policy']
    assert event['sources'] == [{
        'title': 'Graduate stream policy',
        'url': '/documents/abc/download',
        'page': 4,
        'extraction_method': 'document_intelligence',
    }]
