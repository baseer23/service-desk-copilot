"""Status-only readiness CLI. No Azure request without --live; no microphone code."""

import argparse
import asyncio
import os
import shutil
import subprocess
from contextlib import redirect_stderr
from typing import Literal

from pydantic import BaseModel, ValidationError

from backend.app.core.config import Settings

from .readiness import ReadinessReport, StoreCheck, auth_method, configuration_readiness


class Probe(BaseModel):
    status: Literal["ok"]


async def local_checks(settings: Settings) -> tuple[StoreCheck, StoreCheck]:
    from backend.app.mcp.client import DeskMateMCPClient

    # SDK/subprocess diagnostics remain private; only fixed statuses leave the CLI.
    with open(os.devnull, "w") as sink, redirect_stderr(sink):
        client = DeskMateMCPClient(settings, stderr=sink)
        try:
            await asyncio.wait_for(client.start(), 15)
            result = await asyncio.wait_for(client.call_tool("get_local_storage_status", {}), 15)
            stores = result["result"]
            return "READY", "READY" if stores.get("chroma") and stores.get("sqlite") else "ERROR"
        except Exception:  # noqa: BLE001 - status-only CLI boundary
            return "ERROR", "ERROR"
        finally:
            try:
                await asyncio.wait_for(client.close(), 5)
            except Exception:  # noqa: BLE001 - private subprocess diagnostics
                return "ERROR", "ERROR"


async def validate_live(settings: Settings, report: ReadinessReport) -> ReadinessReport:
    """Exactly one reasoning request, one embedding, one silent Voice Live handshake."""
    if (
        settings.test_mode
        or settings.generation_provider != "azure_openai"
        or settings.embed_provider != "azure_openai"
        or settings.voice_provider != "azure_voicelive"
    ):
        raise ValueError("LIVE_REQUIRES_AZURE_MODE")
    if not all(cap.configured for cap in (report.reasoning, report.embeddings, report.voice)):
        raise ValueError("LIVE_CONFIGURATION_MISSING")
    import logging

    from azure.identity.aio import DefaultAzureCredential

    from backend.app.adapters.embeddings import AzureEmbeddingProvider
    from backend.app.models.provider_azure_openai import AzureOpenAIProvider
    from backend.app.voice.azure_voicelive import AzureVoiceLiveProvider

    for namespace in ("azure", "openai", "httpx", "httpcore"):
        logger = logging.getLogger(namespace)
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
    reasoning = None
    voice = None
    current = report.reasoning
    try:
        if settings.azure_use_entra_id:
            async with DefaultAzureCredential(
                process_timeout=5, connection_timeout=5, read_timeout=5, retry_total=0
            ) as credential:
                for scope in (
                    "https://cognitiveservices.azure.com/.default",
                    "https://ai.azure.com/.default",
                ):
                    await asyncio.wait_for(credential.get_token(scope), 15)
        reasoning = AzureOpenAIProvider(settings)
        current.locally_initialized = True
        await reasoning.generate_structured(
            "readiness", {"request": "Fictional IT readiness check. Return JSON status ok."}, Probe
        )
        report.reasoning.authenticated = "YES"
        report.reasoning.reachable = "YES"
        report.reasoning.locally_initialized = True
        report.reasoning.live_validated = True
        current = report.embeddings
        current.locally_initialized = True
        embeddings = AzureEmbeddingProvider(settings, client=reasoning.client)
        await asyncio.wait_for(
            asyncio.to_thread(embeddings.embed_texts, ["Fictional service-desk readiness check."]),
            settings.model_timeout_sec + 1,
        )
        report.embeddings.authenticated = "YES"
        report.embeddings.reachable = "YES"
        report.embeddings.locally_initialized = True
        report.embeddings.live_validated = True
        current = report.voice
        current.locally_initialized = True
        voice = AzureVoiceLiveProvider(settings)
        await asyncio.wait_for(voice.create_session(), 25)

        async def configured() -> None:
            async for event in voice.events():
                if event.type == "error":
                    raise ValueError("VOICE_CONFIGURATION_REJECTED")
                if event.type == "configured":
                    return
            raise ValueError("VOICE_DISCONNECTED")

        await asyncio.wait_for(configured(), 10)
        report.voice.authenticated = "YES"
        report.voice.reachable = "YES"
        report.voice.locally_initialized = True
        report.voice.live_validated = True
        report.live_azure_validated = True
        return report
    except Exception:  # noqa: BLE001 - never print raw provider responses
        current.reachable = "NO"
        return report
    finally:
        try:
            if voice:
                await asyncio.wait_for(voice.close_session(), 10)
        except Exception:  # noqa: BLE001 - no raw SDK cleanup errors
            report.voice.live_validated = False
            report.live_azure_validated = False
        if reasoning:
            reasoning.close()


def cli_signed_in(check: bool) -> str:
    executable = shutil.which("az")
    if not check or not executable:
        return "NOT_CHECKED"
    try:
        result = subprocess.run(  # noqa: S603 - fixed read-only CLI command
            [executable, "account", "show", "--output", "none", "--only-show-errors"],
            capture_output=True,
            timeout=8,
            check=False,
        )
        return "YES" if result.returncode == 0 else "NO"
    except (OSError, subprocess.TimeoutExpired):
        return "NO"


def rows(settings: Settings, report: ReadinessReport, signed_in: str) -> dict[str, str]:
    def found(value: object) -> str:
        return "FOUND" if value else "MISSING"

    statuses = {
        "Runtime mode": report.runtime_mode,
        "Azure reasoning endpoint": found(settings.azure_foundry_endpoint),
        "Reasoning deployment": found(settings.azure_model_deployment),
        "Reasoning authentication": auth_method(settings),
        "Azure embedding deployment": found(settings.azure_embedding_deployment),
        "Voice Live enabled": "YES" if settings.voice_enabled else "NO",
        "Voice Live endpoint": found(settings.azure_voicelive_endpoint),
        "Voice Live model": found(settings.azure_voicelive_model),
        "Voice Live authentication": auth_method(settings, True),
        "Azure CLI executable": found(shutil.which("az")),
        "Azure CLI signed in": signed_in,
        "Local MCP": report.local_mcp,
        "Private stores": report.private_stores,
        "Live Azure validated": "YES" if report.live_azure_validated else "NO",
        "Local Azure usage enabled": "YES" if report.azure_usage.get("enabled") else "NO",
        "Estimated CAD allowance left": (
            f"{report.azure_usage.get('remaining_estimated_cad', 0):.2f}"
        ),
        "Azure invoice hard cap": "NO",
    }
    for name, capability in (
        ("Reasoning", report.reasoning),
        ("Embeddings", report.embeddings),
        ("Voice", report.voice),
    ):
        statuses[f"{name} configured"] = "YES" if capability.configured else "NO"
        statuses[f"{name} locally initialized"] = "YES" if capability.locally_initialized else "NO"
        statuses[f"{name} authenticated"] = capability.authenticated
        statuses[f"{name} reachable"] = capability.reachable
        statuses[f"{name} live validated"] = "YES" if capability.live_validated else "NO"
    return statuses


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Explicitly authorize three bounded Azure checks; no microphone/audio streaming.",
    )
    parser.add_argument(
        "--check-cli",
        action="store_true",
        help="Read local Azure CLI account status; never print account metadata.",
    )
    args = parser.parse_args(argv)
    try:
        settings = Settings()
    except ValidationError:
        print("Runtime mode                     ERROR")
        print("Configuration                    INVALID")
        print("Live Azure validated             NO")
        return 2
    report = configuration_readiness(settings)
    local, stores = asyncio.run(local_checks(settings))
    report.local_mcp = local
    report.private_stores = stores
    if args.live:
        try:
            report = asyncio.run(validate_live(settings, report))
        except ValueError:
            print("Live validation                  CONFIGURATION_ERROR")
    for label, status in rows(settings, report, cli_signed_in(args.check_cli)).items():
        print(f"{label:<33}{status}")
    ready = all(cap.configured for cap in (report.reasoning, report.embeddings, report.voice))
    success = (not args.live or report.live_azure_validated) and ready
    return 0 if success and local == "READY" and stores == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
