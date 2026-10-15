import sqlite3
from typing import TypedDict

import pytest
from backend.app.agent.checkpoints_records import LocalCheckpointSaver
from backend.app.core.record_usage import TABLES, LocalUsageGuard
from backend.app.core.azure_usage import AzureUsageBlocked, AzureUsageGuard
from backend.app.core.principal import Principal, principal_scope, sign_principal, verify_principal
from backend.app.repositories.record_memory import RecordCaseMemoryStore
from backend.app.store.local_records import LocalRecords
from backend.app.store.records import ManagedStoreError
from backend.app.voice.auth import Actor, VoiceError
from backend.app.voice.record_repository import RecordVoiceConversationRepository
from backend.app.voice.models import VoiceStatus
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


def records(settings):
    return LocalRecords(settings)


def test_managed_case_tenant_isolation_cas_and_facts(settings):
    data = records(settings)
    memory = RecordCaseMemoryStore(data)
    with principal_scope(Principal("tenant-a", "user-a")):
        memory.save_case(
            "same-id", {"case_id": "same-id", "ticket": {"summary": "Fictional MFA failure"}}
        )
        memory.save_fact(
            {
                "case_id": "same-id",
                "subject": "Fictional User",
                "predicate": "mfa_error",
                "value": "No notification",
                "source": "human_confirmed",
                "observed_at": "2026-10-13T00:00:00Z",
            }
        )
        memory.record_event(
            "same-id", "human_input", "user", "An actual test confirmation", "one-event"
        )
        memory.record_event("same-id", "human_input", "user", "Duplicate callback", "one-event")
        assert len(memory.list_events("same-id")) == 1
        previous = data.read("case", "same-id")
        memory.save_case(
            "same-id", {"case_id": "same-id", "ticket": {"summary": "Updated fictional issue"}}
        )
        with pytest.raises(ManagedStoreError, match="concurrently"):
            data.write("case", "same-id", {}, previous=previous)
    with principal_scope(Principal("tenant-b", "user-b")):
        assert memory.load_case("same-id") is None and memory.search_facts("Fictional User") == []
        memory.save_case(
            "same-id", {"case_id": "same-id", "ticket": {"summary": "Separate tenant"}}
        )
    with principal_scope(Principal("tenant-a", "user-a")):
        assert memory.load_case("same-id")["ticket"]["summary"] == "Updated fictional issue"


def test_failed_managed_transaction_discards_changes_and_releases_own_lease(settings):
    data = records(settings)
    data.write("usage_policy", "subscription", {"value": "preserved"}, tenant="system:usage")
    with pytest.raises(ValueError):
        with data.locked("usage_policy", "subscription", tenant="system:usage") as payload:
            payload["value"] = "discard-me"
            raise ValueError("Rejected operation")
    with data.locked("usage_policy", "subscription", tenant="system:usage") as payload:
        assert payload["value"] == "preserved"
    policy = data.read("usage_policy", "subscription", tenant="system:usage")
    assert policy["payload"]["lock_until"] == 0


def test_cloud_usage_diagnostics_do_not_acquire_writer_lease(settings, monkeypatch):
    config = settings.model_copy(update={"azure_usage_guard_enabled": True})
    data = records(config)
    guard = LocalUsageGuard(config, data)
    guard.enable_demo(8, "authorized-test")
    def forbidden(*args, **kwargs):
        raise AssertionError("Diagnostics must only read committed state")
    monkeypatch.setattr(data, "locked", forbidden)
    assert guard.snapshot()["enabled"]
    assert guard.checks() == {}


def test_managed_lease_waits_for_normal_network_latency(settings):
    import concurrent.futures
    import threading
    import time
    data = records(settings)
    acquired = threading.Event()
    def holder():
        with data.locked("usage_policy", "subscription", tenant="system:usage"):
            acquired.set()
            time.sleep(0.4)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(holder)
        assert acquired.wait(2)
        with data.locked("usage_policy", "subscription", tenant="system:usage") as payload:
            payload["verified"] = True
        future.result()
    assert data.read("usage_policy", "subscription", tenant="system:usage")["payload"]["verified"]


def test_azure_http_diagnostics_do_not_propagate_to_application_logs(settings):
    import logging

    from backend.app.core.azure_identity import azure_credential
    credential = azure_credential(settings)
    logger = logging.getLogger("azure")
    assert not logger.propagate
    assert any(isinstance(handler, logging.NullHandler) for handler in logger.handlers)
    credential.close()




@pytest.mark.asyncio
async def test_cloud_checkpoint_interrupt_survives_saver_and_graph_restart(settings):
    data = records(settings)

    class State(TypedDict):
        answer: str

    def question(state):
        return {"answer": interrupt("What exact error appears?")}

    def graph():
        builder = StateGraph(State)
        builder.add_node("question", question)
        builder.add_edge(START, "question")
        builder.add_edge("question", END)
        return builder.compile(checkpointer=LocalCheckpointSaver(data))

    config = {"configurable": {"thread_id": "persistent-fictional-case"}}
    with principal_scope(Principal("tenant-a", "user-a")):
        first = graph()
        await first.ainvoke({"answer": ""}, config)
        assert (await first.aget_state(config)).next == ("question",)
        restored = graph()
        result = await restored.ainvoke(Command(resume="Fictional MFA prompt missing"), config)
        assert result["answer"] == "Fictional MFA prompt missing"
    with principal_scope(Principal("tenant-b", "user-b")):
        assert await LocalCheckpointSaver(data).aget_tuple(config) is None


@pytest.mark.asyncio
async def test_checkpoint_channels_share_one_record_and_keep_first_write(settings):
    data = records(settings)
    saver = LocalCheckpointSaver(data)
    config = {"configurable": {"thread_id": "batch-test", "checkpoint_id": "checkpoint-one"}}
    await saver.aput_writes(config, [("first", "original"), ("second", "retained")], "task-one")
    await saver.aput_writes(
        config, [("first", "must-not-overwrite"), ("second", "also-ignore")], "task-one"
    )
    rows = data.query("checkpoint_write", fields={"thread_id": "batch-test"})
    assert len(rows) == 1
    writes = rows[0]["payload"]["writes"]
    assert [saver._load(entry["value"]) for entry in writes] == ["original", "retained"]
    assert {entry["index"] for entry in writes} == {0, 1}


def test_checkpoint_keys_do_not_alias_threads_and_namespaces():
    a = {"configurable": {"thread_id": "one:two", "checkpoint_ns": "three"}}
    b = {"configurable": {"thread_id": "one", "checkpoint_ns": "two:three"}}
    assert LocalCheckpointSaver._key(a, "same") != LocalCheckpointSaver._key(b, "same")


def test_managed_demo_policy_caps_both_fast_checks_and_atomic_reservations(settings):
    config = settings.model_copy(update={
        "azure_usage_guard_enabled": True, "azure_estimated_monthly_app_limit_cad": 50,
    })
    data = records(config)
    guard = LocalUsageGuard(config, data)
    guard.enable_demo(0.01, "user-authorized-demo")
    token = guard.reserve("reasoning", 500, 100)
    guard.finish(token)
    restored = LocalUsageGuard(config, data)
    assert restored.snapshot()["limit_cad"] == 0.01
    restored.require_enabled()
    with pytest.raises(AzureUsageBlocked, match="allowance"):
        restored.reserve("reasoning", 2000, 100)
    with restored.transaction() as db:
        db.execute("UPDATE azure_usage SET estimate=10000 WHERE id=?", (token,))
    with pytest.raises(AzureUsageBlocked, match="allowance"):
        restored.require_enabled()


def test_updated_infrastructure_forecast_cannot_bypass_the_persistent_allowance(settings):
    config = settings.model_copy(update={
        "azure_usage_guard_enabled": True, "azure_estimated_monthly_app_limit_cad": 150,
    })
    data = records(config)
    initial = LocalUsageGuard(config, data)
    initial.enable_demo(50, "approved-original-allowance")
    expanded = LocalUsageGuard(
        config.model_copy(update={"test_mode": False, "azure_estimated_infrastructure_cad": 90}),
        data,
    )
    assert not expanded.snapshot()["enabled"]
    with pytest.raises(AzureUsageBlocked, match="allowance"):
        expanded.require_enabled()
    stored = data.read("usage_policy", "subscription", tenant="system:usage")
    assert stored["payload"]["tables"]["azure_usage"] == []


def test_managed_voice_case_history_remains_owner_scoped_behind_mcp(settings):
    data = records(settings)
    memory = RecordCaseMemoryStore(data)
    with principal_scope(Principal("tenant-a", "owner")):
        data.write("voice", "one", {"state": {}}, fields={"owner_id": "owner"})
        memory.save_case("voice-one", {"case_id": "voice-one", "voice_intake": {"contact": {"email": "fictional@example.invalid"}}})
        memory.record_event("voice-one", "voice_intake", "human", "Fictional private confirmation")
        assert memory.load_case("voice-one") and memory.list_events("voice-one")
    with principal_scope(Principal("tenant-a", "another-user")):
        assert memory.list_cases() == []
        with pytest.raises(PermissionError):
            memory.load_case("voice-one")
        with pytest.raises(PermissionError):
            memory.list_events("voice-one")


def test_cloud_usage_preserves_original_ledger_emergency_and_estimates(settings):
    local = settings.model_copy(update={"azure_usage_guard_enabled": True})
    guard = AzureUsageGuard(local)
    token = guard.reserve("reasoning", 100, 50)
    guard.finish(token, {"prompt_tokens": 20, "completion_tokens": 10})
    guard.disable()
    with sqlite3.connect(guard.path) as db:
        tables = {}
        for table in TABLES:
            cursor = db.execute(f"SELECT * FROM {table}")
            names = [column[0] for column in cursor.description]
            tables[table] = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
    data = records(settings)
    data.write("usage_policy", "subscription", {"tables": tables}, tenant="system:usage")
    native = LocalUsageGuard(local.model_copy(update={"local_persistence_enabled": True}), data)
    assert native.snapshot()["estimated_used_cad"] == guard.snapshot()["estimated_used_cad"]
    assert not native.snapshot()["enabled"]
    with pytest.raises(AzureUsageBlocked, match="disabled"):
        native.reserve("reasoning", 10, 10)


def test_signed_authority_cannot_be_forged_or_switched(settings):
    from pydantic import SecretStr

    config = settings.model_copy(
        update={
            "voice_session_signing_key": SecretStr("fictional-unit-signing-key-xxxxxxxxxxxxxxxx")
        }
    )
    signed = sign_principal(config, Principal("tenant-a", "user-a"))
    assert verify_principal(config, signed).tenant_id == "tenant-a"
    with pytest.raises(PermissionError):
        verify_principal(config, signed.replace(".", "A.", 1))
    with pytest.raises(PermissionError):
        verify_principal(config, sign_principal(config, Principal("tenant-a", "user-a"), 1))


def test_managed_guard_reserves_each_months_infrastructure_without_enabling_dispatch(
    settings, monkeypatch
):
    from datetime import UTC, datetime

    from backend.app.core import record_usage

    data = records(settings)
    config = settings.model_copy(update={"test_mode": False, "azure_usage_guard_enabled": True})
    guard = LocalUsageGuard(config, data)
    assert guard.snapshot()["estimated_used_cad"] == 0.25
    assert not guard.snapshot()["enabled"]

    class NextMonth(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2027, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(record_usage, "datetime", NextMonth)
    from backend.app.core import azure_usage

    monkeypatch.setattr(azure_usage, "datetime", NextMonth)
    assert guard.snapshot()["estimated_used_cad"] == 0.25
    assert not guard.snapshot()["enabled"]


def test_cloud_voice_owner_leases_terminal_updates_and_duplicate_events(settings):
    data = records(settings)
    repo = RecordVoiceConversationRepository(data)
    actor = Actor("user-a", "tenant-a")
    with principal_scope(Principal(actor.tenant_id, actor.owner_id)):
        state = repo.create(actor)
        repo.claim(state.session_id, actor, "lease-one")
        with pytest.raises(VoiceError):
            repo.claim(state.session_id, actor, "lease-two")
        with pytest.raises(VoiceError):
            repo.get(state.session_id, Actor("user-b", "tenant-a"))
        repo.update(
            state.session_id,
            actor,
            lambda state: setattr(state, "status", VoiceStatus.LISTENING),
            "lease-one",
            "event-one",
        )
        previous = repo.get(state.session_id, actor).version
        repo.update(
            state.session_id,
            actor,
            lambda state: setattr(state, "status", VoiceStatus.LISTENING),
            "lease-one",
            "event-one",
        )
        assert repo.get(state.session_id, actor).version == previous
        repo.update(
            state.session_id,
            actor,
            lambda state: setattr(state, "status", VoiceStatus.STOPPED),
            "lease-one",
            "stop",
        )
        with pytest.raises(VoiceError, match="terminal"):
            repo.update(
                state.session_id,
                actor,
                lambda state: setattr(state, "status", VoiceStatus.LISTENING),
                "lease-one",
                "late",
            )
