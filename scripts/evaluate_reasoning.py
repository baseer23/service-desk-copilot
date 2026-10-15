"""Opt-in real-provider evaluation with private, isolated cases and no fixtures.

This runs actual LangGraph, LangChain and MCP, using empty temporary knowledge.
It does not populate the normal UI. A passing check establishes honest handling
of absent diagnostic evidence, not the accuracy of an unverified root cause.

Example (makes billable inference requests, subject to existing controls):
  PYTHONPATH=. .venv/bin/python scripts/evaluate_reasoning.py \
    --provider groq --output /private/tmp/deskmate-evaluation/report.json --live

Azure uses the ORIGINAL ledger even though investigation records are isolated.
Never run this command merely to generate an offline report.
"""

import argparse
import asyncio
import json
import os
import tempfile
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from backend.app.agent.activity import activity_scope
from backend.app.agent.checkpoints import checkpoint_store
from backend.app.agent.graph import ServiceDeskAgent
from backend.app.core.config import Settings
from backend.app.core.principal import Principal, principal_scope
from backend.app.mcp.client import DeskMateMCPClient
from backend.app.models.provider import LLMError
from backend.app.models.provider_routing import SelectableReasoningProvider, provider_scope

SCENARIOS = [
    (
        "m365_mfa",
        "Microsoft 365 MFA authentication failure",
        "Microsoft 365 sign-in fails after I approve the MFA notification. "
        "The sign-in page repeats the MFA prompt. The exact error code is not available.",
    ),
    (
        "vpn",
        "VPN authentication failure",
        "The VPN client reports authentication failed. Other internet access works. "
        "I do not yet have the VPN server logs or an exact error code.",
    ),
    (
        "printer",
        "Network printer unavailable",
        "A network printer appears offline and print jobs stay queued. "
        "I have not verified whether other users or other devices can print.",
    ),
    (
        "ad_lockout",
        "Active Directory account lockout",
        "My Active Directory account keeps locking after it is unlocked. "
        "I do not have domain controller logs or evidence identifying the lockout source.",
    ),
    (
        "outlook",
        "Outlook connectivity failure",
        "Outlook desktop displays Trying to connect. The cause and whether "
        "Outlook on the web works have not yet been established.",
    ),
    (
        "internet_outage",
        "Multiple users reporting internet outages",
        "Multiple users at the same office report that internet access stopped. "
        "We have no monitoring results, network logs or confirmed cause yet.",
    ),
    (
        "access_denied",
        "Application access denied",
        "A business application returns Access denied after sign-in. "
        "I have not provided an application name, authorization records "
        "or group membership evidence.",
    ),
    (
        "wifi",
        "Corporate Wi-Fi connection failure",
        "My laptop cannot connect to the corporate Wi-Fi network. "
        "It is not yet known whether other devices are affected "
        "or whether authentication succeeds.",
    ),
    (
        "conflicting_guidance",
        "Contradictory knowledge-base troubleshooting instructions",
        "I report a conflict between two knowledge-base procedures: "
        "one says to reset the client configuration, and the other warns against resetting it. "
        "I have not supplied either article, its version or supporting evidence. "
        "Help determine what to verify before making a change.",
    ),
    (
        "incomplete",
        "Incomplete information with no matching knowledge",
        "An unspecified work service is not working. I have not provided "
        "an application, symptoms, scope or error message. "
        "There is no matching knowledge supplied.",
    ),
]


def write_private(path: Path, report: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".pending")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    os.replace(temporary, path)


def summarize(scenario, provider, state, activity, calls, elapsed, settings):
    scenario_id, name, _ = scenario
    stages = list(
        dict.fromkeys(
            row["stage"]
            for row in activity
            if row.get("kind") == "workflow" and row.get("status") == "running"
        )
    )
    usage = Counter()
    usage_calls = 0
    for row in activity:
        if row.get("kind") == "llm_usage" and row.get("usage"):
            usage_calls += 1
            usage.update(
                {key: value for key, value in row["usage"].items() if isinstance(value, int)}
            )
    evidence = state.get("evidence", [])

    def unavailable(row):
        content = row.get("content") or {}
        return content.get("available") is False or row.get("source_type") == "tool_error"

    diagnostic = [
        row
        for row in evidence
        if row.get("source_type") not in {"human_report", "human_confirmation"}
        and not unavailable(row)
    ]
    verification = state.get("verification", {})
    unsupported = verification.get("unsupported_claims") if "verify" in stages else None
    fixtures = any(
        row.get("source_system") == "local_fixture" and not unavailable(row) for row in evidence
    )
    question = state.get("pending_question")
    final = state.get("final_response") or {}
    proposed = final.get("recommended_next_action") or state.get("recommended_next_action")
    status = state.get("status")
    checks = {
        "real_langgraph_executed": bool(stages),
        "real_mcp_invoked": bool(calls),
        "no_fixture_evidence": not fixtures,
        "no_unsupported_verified_claims": not unsupported if unsupported is not None else None,
        "requested_missing_information": bool(question or final.get("questions_remaining")),
        "recognized_insufficient_evidence": status in {"needs_input", "insufficient_information"}
        or bool(state.get("uncertainty") or final.get("uncertainty")),
        "resolution_not_claimed_verified": state.get("resolution_status", "unconfirmed")
        != "user_confirmed_resolved",
    }
    input_rate = (
        settings.azure_estimated_input_cad_per_million
        if provider == "azure_openai"
        else settings.groq_input_cad_per_million
    )
    output_rate = (
        settings.azure_estimated_output_cad_per_million
        if provider == "azure_openai"
        else settings.groq_output_cad_per_million
    )
    estimate = (
        (usage["input_tokens"] * input_rate + usage["output_tokens"] * output_rate) / 1_000_000
        if usage_calls and input_rate is not None and output_rate is not None
        else None
    )
    return {
        "id": scenario_id,
        "name": name,
        "provider": provider,
        "outcome": (
            "passed_with_scope_limits"
            if status != "error" and all(value is not False for value in checks.values())
            else "review_required"
        ),
        "scope": "Empty isolated knowledge; real connected read capabilities; no fixture evidence",
        "status": status,
        "stages_executed": stages,
        "checks": checks,
        "evidence_retrieved": {
            "total": len(evidence),
            "diagnostic": len(diagnostic),
            "unavailable_tool_results": sum(unavailable(row) for row in evidence),
            "source_types": dict(Counter(row.get("source_type", "unknown") for row in evidence)),
        },
        "mcp_tools_invoked": calls,
        "unsupported_claims_detected": unsupported,
        "recommendation_quality": (
            "Missing-information and provenance checks only; "
            "no verified resolution or root-cause accuracy claimed"
        ),
        "recommended_next_action": proposed,
        "question_for_human": question,
        "llm_usage": {**dict(usage), "calls_with_usage": usage_calls},
        "duration_seconds": round(elapsed, 3),
        "estimated_api_cost_cad": estimate,
        "cost_note": (
            "Conservative application estimate"
            if provider == "azure_openai"
            else "Unknown unless account-specific CAD rates are configured"
        ),
        "verification_limits": (
            [
                "No source articles were injected; contradictory retrieved-document handling "
                "requires the separate isolated fixture regression."
            ]
            if scenario_id == "conflicting_guidance"
            else []
        ),
    }


async def evaluate(
    original: Settings, providers: list[str], output: Path, scenario_ids: set[str] | None = None
):
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix="deskmate-reasoning-evaluation-"))
    tenant = "evaluation-" + uuid.uuid4().hex
    settings = original.model_copy(
        update={
            "test_mode": False,
            "deployment_mode": "local",
            "data_platform": "local",
            "local_persistence_enabled": True,
            "enable_local_fixtures": False,
            "enable_public_web_tools": False,
            "allow_url_ingest": False,
            # An empty, isolated evaluation must not read private production
            # graph relationships into external model prompts.
            "neo4j_uri": "",
            "mcp_servers": [],
            "generation_provider": providers[0],
            "embed_provider": "sentence_transformers",
            "embed_model_name": "all-MiniLM-L6-v2",
            "speech_enabled": False,
            "auth_allowed_tenants": [tenant],
            "case_db_path": root / "state.sqlite3",
            "chroma_dir": root / "chroma",
            "local_documents_dir": root / "documents",
            "max_research_rounds": 0,
        }
    )
    report = {
        "created_utc": datetime.now(UTC).isoformat(),
        "real_inference": True,
        "isolated_store_path": str(root),
        "fixture_data_created": False,
        "private_connected_graph_read": False,
        "azure_financial_history_preserved": True,
        "scenarios": [],
    }
    router = SelectableReasoningProvider(settings)
    if "azure_openai" in providers:
        from backend.app.models.provider_azure_openai import AzureOpenAIProvider

        # Do not create an empty financial ledger alongside isolated test cases.
        router._providers["azure_openai"] = AzureOpenAIProvider(original)
    try:
        with (
            (root / "mcp.private.log").open("w") as mcp_log,
            principal_scope(Principal(tenant, "live-evaluator", True)),
        ):
            mcp = DeskMateMCPClient(settings, stderr=mcp_log)
            await mcp.start()
            try:
                async with checkpoint_store(settings) as saver:
                    agent = ServiceDeskAgent(settings, mcp, router, saver)
                    await agent.initialize()
                    report["discovered_mcp_tools"] = sorted(agent.harness.descriptors)
                    original_invoke = agent.harness.invoke
                    calls = []

                    async def tracked(name, arguments):
                        started = time.monotonic()
                        row = {"tool": name, "status": "running"}
                        calls.append(row)
                        try:
                            result = await original_invoke(name, arguments)
                            row.update(
                                status="completed",
                                returned_items=(
                                    len(result.get("items", []))
                                    if isinstance(result, dict)
                                    else None
                                ),
                            )
                            return result
                        except Exception:
                            row["status"] = "failed"
                            raise
                        finally:
                            row["duration_ms"] = round((time.monotonic() - started) * 1000)

                    agent.harness.invoke = tracked
                    for provider in providers:
                        for scenario in SCENARIOS:
                            if scenario_ids and scenario[0] not in scenario_ids:
                                continue
                            calls = []
                            activity = []
                            started = time.monotonic()
                            try:
                                with (
                                    provider_scope(provider, activity.append),
                                    activity_scope(activity.append),
                                ):
                                    state, _ = await asyncio.wait_for(
                                        agent.run_request(uuid.uuid4().hex, summary=scenario[2]),
                                        timeout=600,
                                    )
                                result = summarize(
                                    scenario,
                                    provider,
                                    state,
                                    activity,
                                    calls,
                                    time.monotonic() - started,
                                    settings,
                                )
                            except Exception as error:  # noqa: BLE001 - Sanitized report boundary.
                                # SDK exception strings may contain request bodies;
                                # record only already-sanitized provider failures.
                                result = {
                                    "id": scenario[0],
                                    "name": scenario[1],
                                    "provider": provider,
                                    "outcome": "failed",
                                    "error": (
                                        str(error)
                                        if isinstance(error, LLMError)
                                        else type(error).__name__
                                    ),
                                    "duration_seconds": round(time.monotonic() - started, 3),
                                    "mcp_tools_invoked": calls,
                                    "stages_executed": list(
                                        dict.fromkeys(
                                            row.get("stage")
                                            for row in activity
                                            if row.get("kind") == "workflow"
                                        )
                                    ),
                                }
                            report["scenarios"].append(result)
                            write_private(output, report)
            finally:
                await mcp.close()
    finally:
        await router.aclose()
        from backend.app.core.record_usage import close_usage_clients

        close_usage_clients()
    report["finished_utc"] = datetime.now(UTC).isoformat()
    report["outcome_counts"] = dict(Counter(row["outcome"] for row in report["scenarios"]))
    write_private(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider", action="append", choices=["groq", "azure_openai"], required=True
    )
    parser.add_argument("--scenario", action="append", choices=[row[0] for row in SCENARIOS])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--live", action="store_true", help="Explicitly dispatch real model requests"
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("Real inference requires --live; no API calls were made")
    report = asyncio.run(
        evaluate(
            Settings(),
            list(dict.fromkeys(args.provider)),
            args.output,
            set(args.scenario) if args.scenario else None,
        )
    )
    print(json.dumps({"report": str(args.output), "outcome_counts": report["outcome_counts"]}))


if __name__ == "__main__":
    main()
