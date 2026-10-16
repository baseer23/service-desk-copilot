from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError
from backend.app.conversation.interpreter import contextual_issue
from backend.app.conversation.models import ConversationSession, EntityOperation, InterpretedTurn
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
    a = turn(client, sid, "My e-mail is Abdulbasir at ngenuity.ca.", 1, "failure-a")
    assert a["email"] == "abdulbasir@ngenuity.ca"
    b = turn(client, sid, "No, it is just Abdul Basir before the at symbol.", 2, "failure-b")
    assert b["email"] == "abdulbasir@ngenuity.ca" and "right" in b["response"].lower()
    c = turn(
        client, sid, "No. My full email address is Abdul Basir at ngenuity.ca.", 3, "failure-c"
    )
    assert c["email"] == "abdulbasir@ngenuity.ca"
    d = turn(client, sid, "Basir is actually B A S E E R.", 4, "failure-d")
    assert d["email"] == "abdulbaseer@ngenuity.ca"
    e = turn(client, sid, "Yes, that is correct.", 5, "failure-e")
    assert e["email"] == "abdulbaseer@ngenuity.ca" and e["email_confirmed"]
    assert e["response"] == "Thanks. Do you have your ticket number?"


def test_contextual_validation_rejects_substring_overwrite_without_mutation():
    session = ConversationSession(session_id="context-test")
    session.entities["contact.email.local_part"] = "abdulbasir"
    turn = InterpretedTurn(
        intent="correct_entity",
        operations=[
            EntityOperation(entity="contact.email.local_part", operation="SET", new_value="baseer")
        ],
    )
    assert "complete value" in contextual_issue(turn, session)
    assert session.entities["contact.email.local_part"] == "abdulbasir"


def test_confirmation_intent_cannot_be_a_keep_no_op():
    with pytest.raises(ValidationError, match="CONFIRM"):
        InterpretedTurn(
            intent="confirm_entity",
            operations=[EntityOperation(entity="contact.email.local_part", operation="KEEP")],
        )


def test_every_turn_uses_model_interpretation_with_full_context(make_client, monkeypatch):
    client = make_client()
    provider = client.app.state.agent.provider
    original = provider.generate_structured
    contexts = []

    async def record(stage, context, schema):
        if stage == "conversation_interpret":
            contexts.append(context)
        return await original(stage, context, schema)

    monkeypatch.setattr(provider, "generate_structured", record)
    sid = start(client)
    turn(client, sid, "I'm calling about a ticket.", 0, "context-1")
    turn(client, sid, "My e-mail is Abdulbasir at ngenuity.ca.", 1, "context-2")
    assert len(contexts) == 2
    assert contexts[1]["active_helpdesk_task"] == "ticket"
    assert contexts[1]["conversation_history"]
    assert contexts[1]["entities"] == {}


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
    assert result["version"] == 1


def test_ticket_reference_can_be_corrected_as_generic_entity(make_client):
    client = make_client()
    sid = start(client)
    first = turn(client, sid, "My ticket number is ABC-123.", 0, "ticket-1")
    assert "ABC-123" in first["response"]
    corrected = turn(client, sid, "My ticket number is ABC-124.", 1, "ticket-2")
    assert "ABC-124" in corrected["response"]
