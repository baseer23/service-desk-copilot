import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from backend.app.voice import azure_voicelive
from backend.app.voice.azure_voicelive import AzureVoiceLiveProvider


@pytest.mark.asyncio
async def test_managed_speech_heartbeat_is_shared_and_does_not_block_audio_event_loop(
    settings, monkeypatch
):
    checks = []

    def require():
        checks.append(1)
        time.sleep(0.08)

    monkeypatch.setattr(
        azure_voicelive, "AzureUsageGuard", lambda config: SimpleNamespace(require_enabled=require)
    )
    provider = AzureVoiceLiveProvider(settings.model_copy(update={"data_platform": "azure"}))
    provider.started = time.monotonic()
    append = AsyncMock()
    provider.connection = SimpleNamespace(input_audio_buffer=SimpleNamespace(append=append))
    work = asyncio.gather(provider.send_audio("AAA="), provider.send_audio("AAA="))
    await asyncio.sleep(0.01)
    assert not work.done() and checks == [1]
    await work
    assert append.await_count == 2 and checks == [1]
    await provider.send_audio("AAA=")
    assert checks == [1]


@pytest.mark.asyncio
async def test_cancellation_before_managed_policy_check_finishes_prevents_sdk_response(
    settings, monkeypatch
):
    def require():
        time.sleep(0.08)

    monkeypatch.setattr(
        azure_voicelive, "AzureUsageGuard", lambda config: SimpleNamespace(require_enabled=require)
    )
    provider = AzureVoiceLiveProvider(settings.model_copy(update={"data_platform": "azure"}))
    provider.started = time.monotonic()
    create, cancel = AsyncMock(), AsyncMock()
    provider.connection = SimpleNamespace(response=SimpleNamespace(create=create, cancel=cancel))
    work = asyncio.create_task(provider.respond("Approved fictional guidance"))
    await asyncio.sleep(0.01)
    await provider.interrupt()
    await work
    create.assert_not_awaited()
    cancel.assert_not_awaited()
