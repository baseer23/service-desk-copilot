from backend.app.email_correction import EmailSession, EmailOperation, apply, deterministic_operation


def test_email_correction_operations_are_stateful_and_validated():
    session = EmailSession("test", "abdulbasir", "ngenuity.ca")
    assert apply(session, deterministic_operation("change basir to baseer")) == "Updated to abdulbaseer@ngenuity.ca. Is that correct?"
    assert "does not match" in apply(session, deterministic_operation("replace the E immediately before the @ with R"))
    assert apply(session, deterministic_operation("undo that")) == "Restored abdulbasir@ngenuity.ca."
    assert apply(session, EmailOperation(operation="change_domain"), "mdwlaw.ca") == "Updated to abdulbasir@mdwlaw.ca. Is that correct?"
    assert apply(session, deterministic_operation("yes")) == "Thank you. Your email address has been confirmed."


def test_invalid_assumption_does_not_change_current_username():
    session = EmailSession("test", "abdulbaseer", "ngenuity.ca")
    response = apply(session, EmailOperation(operation="remove_character", expected="z", position="last"))
    assert "did not change" in response
    assert session.email == "abdulbaseer@ngenuity.ca"


def test_spoken_address_shape_is_parsed_without_assuming_domain():
    from backend.app.email_correction import speech_email

    assert speech_email("Abdul Basir at Ingenuity dot C A") == ("abdulbasir", "ingenuity.ca")


def test_api_uses_mcp_domain_registry_and_keeps_session_state(make_client):
    client = make_client()
    started = client.post("/email-correction/start")
    assert started.status_code == 200
    session_id = started.json()["session_id"]
    address = client.post(
        f"/email-correction/{session_id}",
        json={"summary": "Abdul Basir at Ingenuity dot C A"},
    )
    assert address.json()["email"] == "abdulbasir@ngenuity.ca"
    changed = client.post(
        f"/email-correction/{session_id}",
        json={"summary": "change the domain to MDW Law"},
    )
    assert changed.json()["email"] == "abdulbasir@mdwlaw.ca"
    assert changed.json()["domain_lookup"] == "resolved"


def test_voice_conversation_starts_with_helpdesk_greeting_and_uses_selected_provider(make_client):
    client = make_client()
    started = client.post("/voice-conversation/start")
    assert started.status_code == 200
    assert started.json()["response"] == "Hello, welcome to Digital nGenuity support. How can I help you today?"
    reply = client.post(
        f"/voice-conversation/{started.json()['session_id']}",
        json={"summary": "My VPN is not connecting."},
    )
    assert reply.status_code == 200
    assert reply.json()["response"] == "I can help troubleshoot that. What error do you see?"
