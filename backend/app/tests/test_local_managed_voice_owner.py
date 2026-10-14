from backend.app.core.principal import current_principal
from backend.app.voice.auth import COOKIE_NAME, Actor, issue_actor


def test_local_managed_voice_case_routes_forward_the_cookie_owner_to_mcp(make_client):
    client = make_client(
        {"VOICE_SESSION_SIGNING_KEY": "isolated-owner-signing-key-xxxxxxxxxxxxxxxxxxxxxxxx"}
    )
    service = client.app.state.voice
    settings = service.settings
    actor = Actor("a" * 32, settings.voice_tenant_id)
    state = service.repository.create(actor)
    identifier = "voice-" + state.session_id
    settings.data_platform = "azure"
    settings.auth_allowed_tenants = [actor.tenant_id]
    original = client.app.state.agent.harness.invoke
    observed = []

    async def invoke(name, arguments):
        if name in {"get_case", "list_cases"}:
            observed.append(current_principal(settings).owner_id)
            value = {"case_id": identifier, "ticket": {"summary": "Fictional VPN"}, "status": "new"}
            return {"result": value} if name == "get_case" else {"items": [value]}
        return await original(name, arguments)

    client.app.state.agent.harness.invoke = invoke
    client.cookies.set(COOKIE_NAME, issue_actor(settings, actor.owner_id, actor.tenant_id))
    assert client.get("/cases/" + identifier).status_code == 200
    assert client.get("/cases").json()[0]["case_id"] == identifier
    assert observed == [actor.owner_id, actor.owner_id]
    client.cookies.set(COOKIE_NAME, issue_actor(settings, "b" * 32, actor.tenant_id))
    assert client.get("/cases/" + identifier).status_code in {403, 404}
    assert client.get("/cases").json() == []
