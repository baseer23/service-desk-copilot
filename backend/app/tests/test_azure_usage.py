"""Real SQLite/stdio policy regressions; external Azure SDK requests are mocked."""

import asyncio
import concurrent.futures
import sqlite3
import threading
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest
from backend.app.core.azure_usage import AzureUsageBlocked, AzureUsageGuard
from backend.app.core.config import Settings
from backend.app.models.provider import LLMError


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
        again.reserve("embedding", 2000)
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


def test_global_session_daily_and_user_turn_limits(settings):
    guard = AzureUsageGuard(enabled(settings))
    first = guard.reserve("session_text")
    with pytest.raises(AzureUsageBlocked, match="one live"):
        AzureUsageGuard(guard.settings).reserve("session_voice")
    for _ in range(20):
        guard.turn(first)
    with pytest.raises(AzureUsageBlocked, match="turn limit"):
        guard.turn(first)
    guard.finish(first, seconds=12)
    second = guard.reserve("session_text")
    guard.finish(second)
    with pytest.raises(AzureUsageBlocked, match="Daily"):
        guard.reserve("session_text")


def test_emergency_disable_persists_across_instances_and_restart(settings):
    guard = AzureUsageGuard(enabled(settings))
    guard.disable()
    other = AzureUsageGuard(enabled(settings))
    assert not other.snapshot()["enabled"]
    with pytest.raises(AzureUsageBlocked, match="disabled"):
        other.reserve("embedding", 100)


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
        restored.reserve("embedding", 2000)
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
        guard.reserve("embedding", 100)
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
    token = guard.reserve("embedding", 1000)
    guard.finish(token)
    assert guard.snapshot()["estimated_used_cad"] == 0.001
    Clock.current = datetime(2026, 11, 1, tzinfo=UTC)
    assert guard.snapshot()["estimated_used_cad"] == 0
    guard.disable()
    Clock.current = datetime(2026, 12, 1, tzinfo=UTC)
    assert not guard.snapshot()["enabled"]


def test_only_one_fixture_and_live_test_disables_web(settings):
    config = Settings(
        _env_file=None,
        test_mode=True,
        generation_provider="stub",
        embed_provider="stub",
        voice_provider="fake",
        azure_live_test_mode=True,
        azure_usage_guard_enabled=True,
        case_db_path=settings.case_db_path,
        chroma_dir=settings.chroma_dir,
    )
    assert not config.allow_url_ingest and config.max_research_rounds == 0
    assert config.voice_session_timeout_sec <= 600
    guard = AzureUsageGuard(config)
    guard.claim_fixture("fictional VPN")
    guard.claim_fixture("fictional VPN")
    with pytest.raises(AzureUsageBlocked, match="one fictional"):
        guard.claim_fixture("another document")


@pytest.mark.asyncio
async def test_disabled_model_and_embedding_never_dispatch_sdk(settings):
    from backend.app.adapters.embeddings import AzureEmbeddingProvider
    from backend.app.diagnostics.azure_readiness import Probe
    from backend.app.models.provider_azure_openai import AzureOpenAIProvider

    def forbidden(**kwargs):
        raise AssertionError("No Azure request is authorized")

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=forbidden)),
        embeddings=SimpleNamespace(create=forbidden),
    )
    config = settings.model_copy(
        update={
            "azure_foundry_endpoint": "https://test.openai.azure.com",
            "azure_model_deployment": "test",
            "azure_embedding_deployment": "test",
        }
    )
    with pytest.raises(LLMError, match="disabled"):
        await AzureOpenAIProvider(config, client).generate_structured("readiness", {}, Probe)
    with pytest.raises(AzureUsageBlocked, match="disabled"):
        AzureEmbeddingProvider(config, client).embed_texts(["fictional"])


@pytest.mark.asyncio
async def test_model_records_usage_after_awaiting_task_is_cancelled(settings):
    from backend.app.diagnostics.azure_readiness import Probe
    from backend.app.models.provider_azure_openai import AzureOpenAIProvider

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
        AzureUsageGuard(config).reserve("embedding", 100)
    release.set()
    assert await asyncio.to_thread(settled.wait, 5)
    assert model.usage_guard.snapshot()["reported_tokens"]["input_tokens"] == 40


@pytest.mark.asyncio
async def test_sdk_voice_limits_usage_duplicates_and_emergency_close(settings):
    from backend.app.voice.azure_voicelive import AzureVoiceLiveProvider

    context = AsyncMock()
    connection = context.__aenter__.return_value
    config = enabled(settings, azure_voicelive_endpoint="https://test.services.ai.azure.com")
    provider = AzureVoiceLiveProvider(
        config, connector=lambda **kwargs: context, credential=object()
    )
    await provider.create_session()
    token = provider.usage_token
    provider.usage_guard.voice_usage(
        token,
        "response1",
        {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30, "private": "secret"},
    )
    provider.usage_guard.voice_usage(
        token, "response1", {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}
    )
    assert provider.usage_guard.snapshot()["reported_tokens"]["total_tokens"] == 30
    AzureUsageGuard(config).disable()
    with pytest.raises(AzureUsageBlocked, match="disabled"):
        await provider.send_audio("AAA=")
    await asyncio.sleep(0.4)
    assert provider.connection is None
    context.__aexit__.assert_awaited_once()
    connection.input_audio_buffer.append.assert_not_called()
    await provider.close_session()
    assert provider.usage_guard.snapshot()["estimated_used_cad"] == 5


@pytest.mark.asyncio
async def test_voice_duration_is_enforced_by_real_adapter(settings):
    from backend.app.voice.azure_voicelive import AzureVoiceLiveProvider

    context = AsyncMock()
    config = enabled(settings, azure_voicelive_endpoint="https://test.services.ai.azure.com")
    provider = AzureVoiceLiveProvider(
        config, connector=lambda **kwargs: context, credential=object()
    )
    await provider.create_session()
    provider.started = time.monotonic() - 601
    with pytest.raises(RuntimeError, match="duration"):
        await provider.send_audio("AAA=")
    await asyncio.sleep(0.4)
    assert provider.connection is None


def test_emergency_http_checks_origin_and_persists(make_client):
    client = make_client()
    assert (
        client.post("/azure/usage/disable", headers={"Origin": "https://evil.example"}).status_code
        == 403
    )
    response = client.post("/azure/usage/disable", headers={"Origin": "http://testserver"})
    assert response.status_code == 200 and not response.json()["azure_usage"]["enabled"]
    assert not client.get("/admin/readiness").json()["azure_usage"]["invoice_cap"]


@pytest.mark.asyncio
async def test_real_mcp_blocks_public_web_in_acceptance_and_shares_guard(settings):
    from backend.app.mcp.client import DeskMateMCPClient, MCPToolError

    config = enabled(settings, azure_live_test_mode=True)
    client = DeskMateMCPClient(config)
    env = client.subprocess_env()
    assert env["AZURE_LIVE_TEST_MODE"] == "true" and env["AZURE_USAGE_GUARD_ENABLED"] == "true"
    await client.start()
    try:
        for name, args in [
            ("search_web", {"query": "never search"}),
            ("fetch_web_page", {"url": "https://example.com"}),
            ("ingest_url", {"url": "https://example.com"}),
        ]:
            with pytest.raises(MCPToolError, match="disabled"):
                await client.call_tool(name, args)
        result = await client.call_tool(
            "ingest_text",
            {"text": "Fictional VPN: check whether connected.", "title": "Fictional acceptance"},
        )
        assert result["status"] == "ok"
        with pytest.raises(MCPToolError, match="AzureUsageBlocked"):
            await client.call_tool("ingest_text", {"text": "Different fictional document"})
    finally:
        await client.close()


def test_acceptance_pins_one_operator_without_persisting_identity(settings):
    guard = AzureUsageGuard(enabled(settings))
    guard.claim_operator("private-tenant:private-owner")
    guard.claim_operator("private-tenant:private-owner")
    with pytest.raises(AzureUsageBlocked, match="first local tester"):
        guard.claim_operator("private-tenant:different-owner")
    with sqlite3.connect(guard.path) as db:
        assert "private-owner" not in str(
            db.execute("SELECT * FROM azure_acceptance_operator").fetchall()
        )


@pytest.mark.asyncio
async def test_actual_sdk_usage_event_is_recorded_and_acceptance_disables_after_close(settings):
    from azure.ai.voicelive.models import ServerEvent
    from backend.app.voice.azure_voicelive import AzureVoiceLiveProvider

    config = enabled(
        settings,
        azure_live_test_mode=True,
        azure_voicelive_endpoint="https://test.services.ai.azure.com",
    )
    context = AsyncMock()
    provider = AzureVoiceLiveProvider(
        config, connector=lambda **kwargs: context, credential=object()
    )
    await provider.create_session()

    class Connection:
        def __aiter__(self):
            async def events():
                yield ServerEvent(
                    {
                        "type": "response.done",
                        "event_id": "usage1",
                        "response": {
                            "id": "r1",
                            "status": "completed",
                            "output": [],
                            "usage": {
                                "total_tokens": 30,
                                "input_tokens": 20,
                                "output_tokens": 10,
                                "input_token_details": {
                                    "audio_tokens": 5,
                                    "text_tokens": 15,
                                    "cached_tokens": 0,
                                },
                                "output_token_details": {"audio_tokens": 6, "text_tokens": 4},
                            },
                        },
                    }
                )

            return events()

    provider.connection = Connection()
    events = [event async for event in provider.events()]
    assert events[0].type == "response.done"
    assert provider.usage_guard.snapshot()["reported_tokens"]["total_tokens"] == 30
    assert (
        provider.usage_guard.snapshot()["reported_tokens"]["input_token_details_audio_tokens"] == 5
    )
    await provider.close_session()
    assert not AzureUsageGuard(config).snapshot()["enabled"]
    with pytest.raises(AzureUsageBlocked, match="disabled"):
        AzureUsageGuard(config).reserve("reasoning", 100, 80)


def test_setup_ceiling_is_shared_and_independent_of_monthly_ceiling(settings):
    config = enabled(settings, azure_setup_validation_mode=True)
    guard = AzureUsageGuard(config)
    first = guard.reserve("reasoning", 32768, 1500)
    guard.finish(first)
    voice = guard.reserve("session_voice")
    guard.finish(voice)
    assert guard.snapshot()["setup_estimated_used_cad"] == 0.88768
    for _ in range(2):
        token = guard.reserve("reasoning", 32768, 1500)
        guard.finish(token)
    with pytest.raises(AzureUsageBlocked, match="setup validation"):
        guard.reserve("reasoning", 32768, 1500)
    demo = AzureUsageGuard(config.model_copy(update={"azure_setup_validation_mode": False}))
    token = demo.reserve("embedding", 100)
    demo.finish(token)
    assert demo.snapshot()["setup_estimated_used_cad"] == 1.66304
    assert demo.snapshot()["estimated_used_cad"] == 1.66314


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


@pytest.mark.asyncio
async def test_synthetic_setup_selects_manual_commit_on_first_sdk_update(settings):
    from azure.ai.voicelive.models import RequestSession
    from backend.app.voice.azure_voicelive import AzureVoiceLiveProvider

    context = AsyncMock()
    config = enabled(
        settings,
        azure_setup_validation_mode=True,
        azure_voicelive_endpoint="https://test.services.ai.azure.com",
    )
    provider = AzureVoiceLiveProvider(
        config, connector=lambda **kwargs: context, credential=object()
    )
    await provider.create_session()
    session = context.__aenter__.return_value.session.update.call_args.kwargs["session"]
    assert isinstance(session, RequestSession)
    assert session.as_dict()["turn_detection"] is None
    await provider.close_session()


def test_shared_pacing_is_non_retrying_and_emergency_stops_waits(settings):
    config = enabled(settings, azure_reasoning_min_interval_seconds=61)
    first = AzureUsageGuard(config)
    assert first.request_delay("reasoning") == 0
    assert 59 < AzureUsageGuard(config).request_delay("reasoning") <= 61
    first.disable()
    with pytest.raises(AzureUsageBlocked, match="disabled"):
        AzureUsageGuard(config).request_delay("reasoning")


def test_readiness_exposes_sanitized_failure_and_blocks_unvalidated_workflow(settings):
    from backend.app.diagnostics.readiness import configuration_readiness

    config = enabled(
        settings,
        test_mode=False,
        generation_provider="azure_openai",
        embed_provider="azure_openai",
        voice_provider="azure_voicelive",
        azure_foundry_endpoint="https://test.openai.azure.com",
        azure_model_deployment="test",
        azure_embedding_deployment="test",
        azure_voicelive_endpoint="https://test.services.ai.azure.com",
        voice_enabled=True,
    )
    guard = AzureUsageGuard(config)
    guard.record_check("reasoning", True)
    guard.record_check("embeddings", True)
    guard.record_check("text_workflow", False, "rate_limit_exceeded")
    report = configuration_readiness(
        config, reasoning_initialized=True, local_mcp="READY", private_stores="READY"
    )
    assert report.reasoning.live_validated and report.embeddings.live_validated
    # Historical failures remain visible but cannot permanently latch off retry
    # after an operator fixes quota/configuration. Dispatch still enforces policy.
    assert report.text_ready and report.voice_ready
    assert report.azure_validation["text_workflow"]["code"] == "rate_limit_exceeded"
    guard.record_check("voice", False, "private@example.com secret")
    assert guard.checks()["voice"]["code"] == "REDACTED"
    guard.record_check("voice_connection", True)
    report = configuration_readiness(config)
    assert report.voice.authenticated == "YES" and report.voice.reachable == "YES"
    assert not report.voice.live_validated and report.voice_validation_scope == "configuration"


def test_azure_error_code_survives_embedding_and_ingestion_wrappers_without_body(settings):
    from backend.app.adapters.embeddings import AzureEmbeddingProvider
    from backend.app.core.azure_errors import azure_error_code

    class SDKFailure(RuntimeError):
        body: ClassVar[dict[str, dict[str, str]]] = {
            "error": {"code": "PermissionDenied", "message": "secret-token private@example.com"}
        }
        status_code = 403

    def create(**kwargs):
        raise SDKFailure("never publish this external body")

    config = enabled(settings, azure_embedding_deployment="test")
    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    with pytest.raises(RuntimeError, match="PermissionDenied") as error:
        AzureEmbeddingProvider(config, client).embed_texts(["Fictional VPN"])
    assert "secret-token" not in str(error.value) and "private@example.com" not in str(error.value)
    try:
        raise RuntimeError("Ingestion failed") from error.value
    except RuntimeError as wrapper:
        assert azure_error_code(wrapper) == "PermissionDenied"
    assert AzureUsageGuard(config).checks()["embeddings"]["code"] == "PermissionDenied"


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
