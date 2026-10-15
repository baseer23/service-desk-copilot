"""Real SQLite/stdio policy regressions; external Azure SDK requests are mocked."""

import asyncio
import concurrent.futures
import sqlite3
import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from backend.app.core.azure_usage import AzureUsageBlocked, AzureUsageGuard
from backend.app.core.config import Settings
from pydantic import BaseModel


def enabled(settings, **extra):
    return settings.model_copy(update={"azure_usage_guard_enabled": True, **extra})


def test_default_disabled_and_empty_ledger_path(settings):
    assert not settings.azure_usage_guard_enabled
    config = Settings(_env_file=None, azure_usage_db_path="")
    assert config.azure_usage_db_path is None
    guard = AzureUsageGuard(settings)
    with pytest.raises(AzureUsageBlocked, match="disabled"):
        guard.reserve("reasoning", 100, 80)
    assert not guard.path.exists()
    assert not guard.snapshot()["enabled"]


def test_atomic_reservations_persist_and_unknown_usage_is_not_refunded(settings):
    config = enabled(settings, azure_estimated_monthly_app_limit_cad=0.01)
    guard = AzureUsageGuard(config)
    reservation = guard.reserve("reasoning", 500, 100)
    guard.finish(reservation)
    assert guard.snapshot()["estimated_used_cad"] == 0.009
    again = AzureUsageGuard(config)
    with pytest.raises(AzureUsageBlocked, match="allowance"):
        again.reserve("reasoning", 2000)
    assert again.snapshot()["remaining_estimated_cad"] == 0.001
    assert guard.path.stat().st_mode & 0o777 == 0o600


def test_complete_reported_tokens_finalize_estimates_without_pii(settings):
    guard = AzureUsageGuard(enabled(settings))
    token = guard.reserve("reasoning", 1000, 100)
    guard.finish(
        token,
        {
            "prompt_tokens": 20,
            "completion_tokens": 2,
            "total_tokens": 22,
            "secret": "never-persist-me",
        },
    )
    snapshot = guard.snapshot()
    assert snapshot["estimated_used_cad"] == 0.00028
    assert snapshot["reported_tokens"] == {
        "input_tokens": 20,
        "output_tokens": 2,
        "total_tokens": 22,
    }
    with sqlite3.connect(guard.path) as db:
        assert "never-persist" not in str(db.execute("SELECT * FROM azure_usage").fetchall())
    partial = guard.reserve("reasoning", 1000, 100)
    guard.finish(partial, {"prompt_tokens": 0})
    assert guard.snapshot()["estimated_used_cad"] == 0.01428


def test_shared_transaction_blocks_concurrent_requests(settings):
    config = enabled(settings)
    gate = threading.Barrier(2)

    def reserve():
        gate.wait()
        try:
            return AzureUsageGuard(config).reserve("reasoning", 100, 80)
        except AzureUsageBlocked:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reserve) for _ in range(2)]
        tokens = [f.result() for f in futures]
    assert len([t for t in tokens if t]) == 1
    AzureUsageGuard(config).finish(next(t for t in tokens if t))


def test_emergency_disable_persists_across_instances_and_restart(settings):
    guard = AzureUsageGuard(enabled(settings))
    guard.disable()
    other = AzureUsageGuard(enabled(settings))
    assert not other.snapshot()["enabled"]
    with pytest.raises(AzureUsageBlocked, match="disabled"):
        other.reserve("reasoning", 100)


def test_explicit_demo_activation_preserves_charges_and_persistent_cap(settings):
    config = enabled(settings, azure_estimated_monthly_app_limit_cad=50)
    guard = AzureUsageGuard(config)
    token = guard.reserve("reasoning", 500, 100)
    guard.finish(token)
    guard.disable()
    guard.enable_demo(0.01, "user-authorized-demo")
    restored = AzureUsageGuard(config)
    assert restored.snapshot()["estimated_used_cad"] == 0.009
    assert restored.snapshot()["limit_cad"] == 0.01
    with pytest.raises(AzureUsageBlocked, match="allowance"):
        restored.reserve("reasoning", 2000)
    lowered = AzureUsageGuard(
        config.model_copy(update={"azure_estimated_monthly_app_limit_cad": 0.005})
    )
    assert lowered.snapshot()["limit_cad"] == 0.005
    assert not lowered.snapshot()["enabled"]
    restored.disable()
    assert not AzureUsageGuard(config).snapshot()["enabled"]


def test_demo_activation_never_clears_active_reservations_or_configuration_shutdown(settings):
    config = enabled(settings, azure_estimated_monthly_app_limit_cad=50)
    guard = AzureUsageGuard(config)
    token = guard.reserve("reasoning", 100, 50)
    guard.disable()
    guard.enable_demo(50, "user-authorized-demo")
    with pytest.raises(AzureUsageBlocked, match="one Azure"):
        guard.reserve("reasoning", 100)
    guard.finish(token)
    disabled = AzureUsageGuard(config.model_copy(update={"azure_emergency_disable": True}))
    with pytest.raises(AzureUsageBlocked, match="configured guard"):
        disabled.enable_demo(50, "user-authorized-demo")
    for value in (51, float("nan"), float("inf"), -1, True):
        with pytest.raises(ValueError):
            guard.enable_demo(value, "user-authorized-demo")


def test_corrupt_ledger_fails_closed(settings):
    guard = AzureUsageGuard(enabled(settings))
    guard.path.write_bytes(b"not a sqlite ledger")
    assert guard.snapshot()["ledger_error"] and not guard.snapshot()["enabled"]
    with pytest.raises(AzureUsageBlocked, match="ledger unavailable"):
        guard.reserve("reasoning", 100, 80)


def test_monthly_allowance_resets_but_emergency_disable_does_not(settings, monkeypatch):
    import backend.app.core.azure_usage as module

    class Clock(datetime):
        current = datetime(2026, 10, 12, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.current

    monkeypatch.setattr(module, "datetime", Clock)
    guard = AzureUsageGuard(enabled(settings))
    token = guard.reserve("reasoning", 1000)
    guard.finish(token)
    assert guard.snapshot()["estimated_used_cad"] == 0.01
    Clock.current = datetime(2026, 11, 1, tzinfo=UTC)
    assert guard.snapshot()["estimated_used_cad"] == 0
    guard.disable()
    Clock.current = datetime(2026, 12, 1, tzinfo=UTC)
    assert not guard.snapshot()["enabled"]


@pytest.mark.asyncio
async def test_model_records_usage_after_awaiting_task_is_cancelled(settings):
    from backend.app.models.provider_azure_openai import AzureOpenAIProvider

    class Probe(BaseModel):
        status: str

    entered, release, settled = threading.Event(), threading.Event(), threading.Event()

    def create(**kwargs):
        entered.set()
        assert release.wait(5)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"status":"ok"}'))],
            usage={"prompt_tokens": 40, "completion_tokens": 5},
        )

    config = enabled(
        settings,
        azure_foundry_endpoint="https://test.openai.azure.com",
        azure_model_deployment="test",
    )
    model = AzureOpenAIProvider(
        config, SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    )
    original_finish = model.usage_guard.finish

    def finish(*args, **kwargs):
        original_finish(*args, **kwargs)
        settled.set()

    model.usage_guard.finish = finish
    task = asyncio.create_task(model.generate_structured("readiness", {}, Probe))
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(AzureUsageBlocked, match="one Azure"):
        AzureUsageGuard(config).reserve("reasoning", 100)
    release.set()
    assert await asyncio.to_thread(settled.wait, 5)
    assert model.usage_guard.snapshot()["reported_tokens"]["input_tokens"] == 40


def test_emergency_http_checks_origin_and_persists(make_client):
    client = make_client()
    assert (
        client.post(
            "/admin/azure/usage/disable", headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    response = client.post("/admin/azure/usage/disable", headers={"Origin": "http://testserver"})
    assert response.status_code == 200 and response.json()["disabled"]
    assert not client.get("/admin/readiness").json()["azure_usage"]["invoice_cap"]


def test_emergency_config_blocks_before_cloud_dispatch(settings):
    guard = AzureUsageGuard(enabled(settings, azure_emergency_disable=True))
    assert not guard.snapshot()["enabled"]
    with pytest.raises(AzureUsageBlocked, match="disabled"):
        guard.reserve("reasoning", 100, 80)


def test_real_openai_sdk_constructor_works_with_mcp_entra_environment(settings, monkeypatch):
    from backend.app.mcp.client import DeskMateMCPClient
    from backend.app.models.provider_azure_openai import AzureOpenAIProvider

    config = enabled(
        settings,
        azure_foundry_endpoint="https://test.openai.azure.com",
        azure_model_deployment="test",
        azure_use_entra_id=True,
        azure_openai_api_key=None,
    )
    env = DeskMateMCPClient(config).subprocess_env()
    assert "AZURE_OPENAI_API_KEY" not in env
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    model = AzureOpenAIProvider(config)
    assert model.client is not None
    model.close()


def test_shared_pacing_is_non_retrying_and_emergency_stops_waits(settings):
    config = enabled(settings, azure_reasoning_min_interval_seconds=61)
    first = AzureUsageGuard(config)
    assert first.request_delay("reasoning") == 0
    assert 59 < AzureUsageGuard(config).request_delay("reasoning") <= 61
    first.disable()
    with pytest.raises(AzureUsageBlocked, match="disabled"):
        AzureUsageGuard(config).request_delay("reasoning")


def test_sqlite_emergency_and_usage_paths_forward_to_mcp(settings):
    from backend.app.mcp.client import DeskMateMCPClient

    config = enabled(
        settings,
        azure_usage_db_path=settings.case_db_path.parent / "separate-usage.sqlite3",
        azure_emergency_disable=True,
        azure_setup_validation_mode=True,
    )
    env = DeskMateMCPClient(config).subprocess_env()
    assert env["AZURE_EMERGENCY_DISABLE"] == "true"
    assert env["AZURE_SETUP_VALIDATION_MODE"] == "true"
    assert env["AZURE_USAGE_DB_PATH"] == str(config.azure_usage_db_path)
    assert env["AZURE_USAGE_GUARD_ENABLED"] == "true"


@pytest.mark.parametrize("kind", ["session_voice", "session_text", "embedding"])
def test_retired_dispatch_categories_rejected_before_ledger_mutation(settings, kind):
    guard = AzureUsageGuard(enabled(settings))
    with pytest.raises(ValueError, match="Unsupported usage category"):
        guard.reserve(kind, 100)
    assert not guard.path.exists()


def test_retired_voice_rows_are_never_refunded_or_rewritten(settings):
    guard = AzureUsageGuard(enabled(settings))
    now = datetime.now(UTC)
    with guard.transaction() as db:
        db.execute(
            "INSERT INTO azure_usage(id,month,day,kind,estimate,active,usage,seconds,turns,phase) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "historical-voice",
                now.strftime("%Y-%m"),
                now.strftime("%Y-%m-%d"),
                "session_voice",
                1_000_000,
                0,
                '{"input_tokens":30}',
                120,
                3,
                "setup",
            ),
        )
        db.execute(
            "INSERT INTO azure_voice_usage VALUES(?,?,?)",
            ("historical-voice", "event-hash", '{"input_tokens":30}'),
        )
        before = db.execute("SELECT * FROM azure_usage WHERE id='historical-voice'").fetchone()
    with pytest.raises(AzureUsageBlocked, match="Only current reasoning"):
        guard.finish("historical-voice", {"prompt_tokens": 0, "completion_tokens": 0})
    token = guard.reserve("reasoning", 100, 80)
    guard.finish(token, {"prompt_tokens": 20, "completion_tokens": 2})
    with guard.transaction() as db:
        assert (
            db.execute("SELECT * FROM azure_usage WHERE id='historical-voice'").fetchone() == before
        )
        assert db.execute("SELECT COUNT(*) FROM azure_voice_usage").fetchone()[0] == 1
    assert guard.snapshot()["estimated_used_cad"] == 1.00028
    assert guard.snapshot()["setup_estimated_used_cad"] == 1


def test_local_record_guard_preserves_financial_history_and_shutdown_on_restart(settings):
    config = enabled(settings, local_persistence_enabled=True)
    guard = AzureUsageGuard(config)
    assert type(guard).__name__ == "LocalUsageGuard"
    guard.enable_demo(8, "isolated-authorized-existing-policy")
    token = guard.reserve("reasoning", 500, 100)
    guard.finish(token)
    guard.disable()
    from backend.app.core.record_usage import close_usage_clients

    close_usage_clients()
    restored = AzureUsageGuard(config)
    assert not restored.snapshot()["enabled"]
    assert restored.snapshot()["estimated_used_cad"] == 0.009
    assert restored.snapshot()["limit_cad"] == 8
    with pytest.raises(AzureUsageBlocked, match="disabled"):
        restored.reserve("reasoning", 100, 80)


def test_setup_reasoning_ceiling_is_preserved_without_voice_billing(settings):
    config = enabled(settings, azure_setup_validation_mode=True)
    guard = AzureUsageGuard(config)
    for _ in range(5):
        token = guard.reserve("reasoning", 32768, 1500)
        guard.finish(token)
    assert guard.snapshot()["setup_estimated_used_cad"] == 1.9384
    with pytest.raises(AzureUsageBlocked, match="setup validation"):
        guard.reserve("reasoning", 32768, 1500)


def test_snapshot_ignores_recovery_audit_metadata_without_changing_history(settings):
    import json

    guard = AzureUsageGuard(enabled(settings))
    token = guard.reserve("reasoning", 500, 100)
    guard.finish(token)
    stored = json.dumps(
        {
            "input_tokens": 20,
            "output_tokens": 2,
            "input_token_details_audio_tokens": 4,
            "recovered_at": "private audit timestamp",
            "exited_process": 123,
            "recovery": {"private": "do not publish"},
        }
    )
    with guard.transaction() as db:
        db.execute("UPDATE azure_usage SET usage=? WHERE id=?", (stored, token))
    snapshot = guard.snapshot()
    assert snapshot["enabled"] and not snapshot["ledger_error"]
    assert snapshot["estimated_used_cad"] == 0.009
    assert snapshot["reported_tokens"] == {
        "input_tokens": 20,
        "output_tokens": 2,
        "input_token_details_audio_tokens": 4,
    }
    with guard.transaction() as db:
        assert (
            db.execute("SELECT usage FROM azure_usage WHERE id=?", (token,)).fetchone()[0] == stored
        )
