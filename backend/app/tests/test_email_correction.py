from concurrent.futures import ThreadPoolExecutor

from backend.app.email_correction import (
    EmailOperation,
    EmailSession,
    EmailSessions,
    apply,
    deterministic_operation,
    speech_email,
)
from backend.app.email_domains import ApprovedDomainRegistry


def test_stateful_username_edits_lock_domain_and_undo():
    session = EmailSession("test", "abdulbasir", "ngenuity.ca", domain_locked=True)
    apply(session, deterministic_operation("change basir to baseer"))
    assert session.email == "abdulbaseer@ngenuity.ca"
    apply(
        session,
        EmailOperation(
            operation="replace_character", expected="e", replacement="r", position="last"
        ),
    )
    assert session.email == "abdulbaserr@ngenuity.ca"
    assert (
        apply(session, deterministic_operation("undo that")) == "Restored abdulbaseer@ngenuity.ca."
    )
    assert session.domain_locked
    assert "does not have" in apply(
        session, EmailOperation(operation="remove_character", expected="z", position="last")
    )


def test_spoken_grouped_letters_and_repeats():
    assert speech_email("It is A BDULBAS EE R at NGENUITY dot C A") == (
        "abdulbaseer",
        "ngenuity.ca",
    )
    operation = deterministic_operation("In BASIR it is actually BAS EE R.")
    assert operation.expected == "basir" and operation.replacement == "baseer"


def test_registry_exact_fuzzy_wrong_tld_reload_and_ambiguity(tmp_path):
    path = tmp_path / "domains.txt"
    path.write_text("# approved\nngenuity.ca\nmdwlaw.ca\n")
    registry = ApprovedDomainRegistry(path)
    assert registry.resolve("N Genuity dot C A").canonical_domain == "ngenuity.ca"
    assert registry.resolve("ingenuity").canonical_domain == "ngenuity.ca"
    assert registry.resolve("ngenuity dot com").clarification_required
    path.write_text("ngenuity.ca\nngenuity.co\n")
    registry.reload()
    ambiguous = registry.resolve("ngenuity")
    assert ambiguous.ambiguous


def test_real_failure_sequence_through_voice_route(make_client):
    client = make_client()
    started = client.post("/voice-conversation/start").json()
    sid = started["session_id"]
    turns = [
        "My e-mail address is Abdul-dussier at Ingenuity.",
        "Actually I meant Abdul Bashir at Ingenuity CA.",
        "It is A BDULBAS EE R at NGENUITY dot CA.",
        "No, it is A BDULBAS EE R. The domain is correct.",
        "In BASIR it is actually BAS EE R.",
        "In the current one, remove the I and put two Es.",
    ]
    states = []
    for index, text in enumerate(turns):
        response = client.post(
            f"/voice-conversation/{sid}",
            json={"summary": text, "request_id": f"request-{index}", "expected_version": index},
        )
        assert response.status_code == 200, response.text
        states.append(response.json())
    print([(state["version"], state["email"], state["domain_locked"], state["operation"]) for state in states])
    assert states[1]["domain"] == "ngenuity.ca"
    assert all(state["domain"] in {None, "ngenuity.ca"} for state in states)
    assert states[-1]["email"] == "abdulbaseer@ngenuity.ca"
    assert states[-1]["domain_locked"]
    assert "Which" in states[-1]["response"]


def test_duplicate_request_and_stale_version_are_rejected(make_client):
    client = make_client()
    sid = client.post("/email-correction/start").json()["session_id"]
    body = {
        "summary": "Abdul Basir at Ingenuity dot C A",
        "request_id": "same-request",
        "expected_version": 0,
    }
    first = client.post(f"/email-correction/{sid}", json=body)
    duplicate = client.post(f"/email-correction/{sid}", json=body)
    assert first.json() == duplicate.json()
    stale = client.post(
        f"/email-correction/{sid}",
        json={"summary": "undo that", "request_id": "other-request", "expected_version": 0},
    )
    assert stale.status_code == 409


def test_one_thousand_independent_sessions():
    sessions = EmailSessions()
    with ThreadPoolExecutor(max_workers=16) as pool:
        values = list(pool.map(lambda _: sessions.create().session_id, range(1000)))
    assert len(set(values)) == 1000


def test_general_voice_stays_general(make_client):
    client = make_client()
    started = client.post("/voice-conversation/start").json()
    reply = client.post(
        f"/voice-conversation/{started['session_id']}",
        json={
            "summary": "My VPN is not connecting.",
            "request_id": "general-1",
            "expected_version": 0,
        },
    )
    assert reply.json()["mode"] == "helpdesk"
