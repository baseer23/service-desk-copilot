from concurrent.futures import ThreadPoolExecutor
from backend.app.conversation.interpreter import deterministic_turn
from backend.app.conversation.models import ConversationSession, EntityOperation
from backend.app.conversation.operations import apply_operation
from backend.app.conversation.sessions import ConversationSessions
from backend.app.resolution.registry import RegistryStore


def start(client):
    return client.post("/voice-conversation/start").json()["session_id"]


def turn(client, sid, text, version, request_id):
    response = client.post(
        f"/voice-conversation/{sid}",
        json={"summary": text, "expected_version": version, "request_id": request_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_five_confirmed_live_regressions_and_ticket_resume(make_client):
    client = make_client()
    sid = start(client)
    assert turn(client, sid, "I'm calling about a ticket.", 0, "ticket-task")["mode"] == "helpdesk"
    a = turn(client, sid, "My e-mail is Abdulbasir at ngenuity.ca.", 0, "failure-a")
    assert a["email"] == "abdulbasir@ngenuity.ca"
    b = turn(client, sid, "No, it is just Abdul Basir before the at symbol.", 1, "failure-b")
    assert b["email"] == "abdulbasir@ngenuity.ca" and "right" in b["response"].lower()
    c = turn(
        client, sid, "No. My full email address is Abdul Basir at ngenuity.ca.", 2, "failure-c"
    )
    assert c["email"] == "abdulbasir@ngenuity.ca"
    d = turn(client, sid, "Basir is actually B A S E E R.", 3, "failure-d")
    assert d["email"] == "abdulbaseer@ngenuity.ca"
    e = turn(client, sid, "Yes, that is correct.", 4, "failure-e")
    assert e["email"] == "abdulbaseer@ngenuity.ca" and e["email_confirmed"]
    assert e["response"] == "Thanks. Do you have your ticket number?"


def test_parser_uses_entity_spans_not_conversation_prefixes():
    for text in [
        "My e-mail is Abdulbasir at ngenuity.ca.",
        "No. My full email address is Abdul Basir at ngenuity.ca.",
    ]:
        parsed = deterministic_turn(text)
        assert parsed.operations[0].new_value == "abdulbasir"


def test_unknown_domain_requires_confirmation_but_remains_usable(make_client):
    client = make_client()
    sid = start(client)
    captured = turn(client, sid, "My email is QA User at customunknown.dev.", 0, "unknown-1")
    assert captured["email"] == "qauser@customunknown.dev" and not captured["email_confirmed"]
    confirmed = turn(client, sid, "Yes, that is correct.", 1, "unknown-2")
    assert confirmed["email_confirmed"]


def test_deduplication_stale_version_and_undo(make_client):
    client = make_client()
    sid = start(client)
    body = {
        "summary": "My email is QA User at ngenuity.ca.",
        "expected_version": 0,
        "request_id": "same-request",
    }
    first = client.post(f"/voice-conversation/{sid}", json=body)
    duplicate = client.post(f"/voice-conversation/{sid}", json=body)
    assert first.json() == duplicate.json()
    assert (
        client.post(
            f"/voice-conversation/{sid}",
            json={"summary": "undo", "expected_version": 0, "request_id": "stale-one"},
        ).status_code
        == 409
    )
    assert turn(client, sid, "undo", 1, "undo-one")["email"] is None


def test_generic_username_insert_delete_and_replace_preserve_domain():
    session = ConversationSession(session_id="operation-test")
    session.entities = {
        "contact.email.local_part": "abdulbasir",
        "contact.email.domain": "ngenuity.ca",
    }
    apply_operation(
        session,
        EntityOperation(
            entity="contact.email.local_part",
            operation="REPLACE",
            old_value="basir",
            new_value="baseer",
        ),
    )
    apply_operation(
        session,
        EntityOperation(
            entity="contact.email.local_part", operation="INSERT", new_value="x", position=5
        ),
    )
    assert session.email == "abdulxbaseer@ngenuity.ca"
    apply_operation(
        session,
        EntityOperation(entity="contact.email.local_part", operation="DELETE", old_value="x"),
    )
    assert session.email == "abdulbaseer@ngenuity.ca"


def test_registry_entries_alias_fuzzy_tld_and_test_only_synthetics(tmp_path):
    path = tmp_path / "domains.txt"
    path.write_text("ngenuity.ca\ncodybrian.ca\n")
    registry = RegistryStore(path, threshold=90)
    assert (
        registry.resolve("contact.email.domain", "Cody Broan dot CA")["canonical_value"]
        == "codybrian.ca"
    )
    wrong_tld = registry.resolve("contact.email.domain", "ngenuity dot com")
    assert wrong_tld["canonical_value"] == "ngenuity.com"
    assert wrong_tld["requires_confirmation"]
    assert not any(value.endswith("example.com") for value in registry.list("domains"))
    demo = RegistryStore(path, test_mode=True)
    assert len([x for x in demo.list("domains") if x.endswith("example.com")]) == 10


def test_one_thousand_independent_sessions():
    sessions = ConversationSessions()
    with ThreadPoolExecutor(max_workers=16) as pool:
        ids = list(pool.map(lambda _: sessions.create().session_id, range(1000)))
    assert len(set(ids)) == 1000


def test_general_vpn_does_not_force_email(make_client):
    client = make_client()
    sid = start(client)
    result = turn(client, sid, "My VPN is not connecting.", 0, "vpn-general")
    assert result["mode"] == "helpdesk" and result.get("email") is None


def test_ticket_reference_can_be_corrected_as_generic_entity(make_client):
    client = make_client()
    sid = start(client)
    first = turn(client, sid, "My ticket number is ABC-123.", 0, "ticket-1")
    assert "ABC-123" in first["response"]
    corrected = turn(client, sid, "My ticket number is ABC-124.", 1, "ticket-2")
    assert "ABC-124" in corrected["response"]
