"""Historical voice cases remain owner-scoped through real MCP after runtime removal."""

from backend.app.core.principal import current_principal, Principal, principal_scope
from backend.app.repositories.record_memory import RecordCaseMemoryStore
from backend.app.store.local_records import LocalRecords
from backend.app.voice.record_repository import RecordVoiceConversationRepository
from backend.app.voice.auth import COOKIE_NAME, Actor, issue_actor


def test_local_historical_voice_routes_forward_cookie_owner_to_real_mcp(make_client):
    client = make_client({
        "LOCAL_PERSISTENCE_ENABLED": "true",
        "VOICE_SESSION_SIGNING_KEY": "isolated-owner-signing-key-xxxxxxxxxxxxxxxxxxxxxxxx",
    })
    settings = client.app.state.settings
    tenant = settings.auth_allowed_tenants[0] if settings.auth_allowed_tenants else settings.voice_tenant_id
    actor = Actor("a" * 32, tenant)
    records = LocalRecords(settings)
    with principal_scope(Principal(actor.tenant_id, actor.owner_id)):
        historical = RecordVoiceConversationRepository(records).create(actor)
        identifier = "voice-" + historical.session_id
        memory = RecordCaseMemoryStore(records)
        memory.save_case(identifier, {
            "case_id": identifier, "ticket": {"summary": "Isolated historical VPN report"}, "status": "new",
        })
    original = client.app.state.agent.harness.invoke
    observed = []
    async def trace(name, arguments):
        if name in {"get_case", "list_cases"}:
            observed.append(current_principal(settings).owner_id)
        return await original(name, arguments)
    client.app.state.agent.harness.invoke = trace
    client.cookies.set(COOKIE_NAME, issue_actor(settings, actor.owner_id, actor.tenant_id))
    assert client.get("/cases/" + identifier).status_code == 200
    assert client.get("/cases").json()[0]["case_id"] == identifier
    assert observed == [actor.owner_id, actor.owner_id]
    client.cookies.set(COOKIE_NAME, issue_actor(settings, "b" * 32, actor.tenant_id))
    assert client.get("/cases/" + identifier).status_code in {401, 403, 404}
    assert client.get("/cases").json() == []
    # Read authorization must never rewrite or purge the preserved transcript.
    assert records.read("voice", historical.session_id, tenant=actor.tenant_id)["payload"]["state"] == historical.model_dump(mode="json")
