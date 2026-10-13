"""Opt-in validation with configured Azure OpenAI and/or the actual public network.

python -m backend.app.tests.run_live_versatility --mode azure_openai --report /tmp/live.json
Never run automatically in pytest; uses fictional tickets and isolated stores.
"""

import argparse
import json
import re
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient
from backend.app.core.config import Settings
from backend.app.main import create_app
from backend.app.tests.scenario_data import SCENARIOS
from backend.app.tests.test_service_desk_scenarios import ScenarioProvider


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["azure_openai", "web"], required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--resume", action="store_true", help="Resume the cases/checkpoints in an existing report"
    )
    parser.add_argument(
        "--pace-seconds",
        type=float,
        default=0,
        help="Validation-only pacing between hosted calls for a low-quota account",
    )
    args = parser.parse_args()
    previous = json.loads(args.report.read_text()) if args.resume else {}
    root = (
        Path(previous["runtime"])
        if previous
        else Path(tempfile.mkdtemp(prefix=f"deskmate-live-{args.mode}-"))
    )
    settings = Settings(
        test_mode=False,
        generation_provider="azure_openai",
        embed_provider="azure_openai",
        neo4j_uri="",
        case_db_path=root / "cases.db",
        chroma_dir=root / "chroma",
        max_research_rounds=1,
    )
    rows = []
    report = {"mode": args.mode, "runtime": str(root), "scenarios": rows}

    def save():
        args.report.write_text(json.dumps(report, indent=2))

    with TestClient(create_app(settings)) as client:
        for scenario in (
            [SCENARIOS[0], SCENARIOS[1], SCENARIOS[2]]
            if args.mode == "azure_openai"
            else [SCENARIOS[9]]
        ):
            old = next(
                (r for r in previous.get("scenarios", []) if r["scenario"] == scenario.name), {}
            )
            if old.get("status") == "PASS":
                rows.append(old)
                save()
                continue
            # Normal MCP ingestion, Azure embeddings and temporary Chroma.
            client.portal.call(
                client.app.state.agent.harness.invoke,
                "ingest_text",
                {
                    "title": f"Fictional internal KB: {scenario.name}",
                    "text": scenario.kb,
                    "source": "Fictional service-desk validation KB",
                    "source_type": "local_fixture",
                },
            )
            if args.mode == "web":
                client.app.state.agent.provider = ScenarioProvider(scenario)
            tools, model_stages = list(old.get("tools", [])), list(old.get("model_stages", []))
            original_protocol = client.app.state.mcp.call_tool
            original_model = client.app.state.agent.provider.generate_structured

            async def track_protocol(name, arguments):
                result = await original_protocol(name, arguments)
                tools.append(
                    {
                        "name": name,
                        "arguments": arguments,
                        "status": result.get("status"),
                        "source_system": result.get("source_system"),
                    }
                )
                return result

            async def track_model(stage, context, schema):
                if args.mode == "azure_openai" and args.pace_seconds:
                    import asyncio

                    print(f"{scenario.name}: pacing before {stage}", flush=True)
                    await asyncio.sleep(args.pace_seconds)
                value = await original_model(stage, context, schema)
                model_stages.append(
                    {
                        "stage": stage,
                        "provider": client.app.state.agent.provider.name(),
                        "output": value.model_dump(),
                    }
                )
                return value

            client.app.state.mcp.call_tool = track_protocol
            client.app.state.agent.provider.generate_structured = track_model
            row = {
                "scenario": scenario.name,
                "status": "RUNNING",
                "tools": tools,
                "model_stages": model_stages,
                "questions": list(old.get("questions", [])),
                "errors": [],
                "previous_errors": old.get("errors", []),
            }
            rows.append(row)
            save()
            print(f"Starting {args.mode} {scenario.name}", flush=True)
            case_id = old.get("case_id")
            row["case_id"] = case_id

            def post(path, payload=None):
                nonlocal case_id
                response = client.post(path, json=payload)
                # Retry the durable graph checkpoint only for reported quota errors.
                for _ in range(5):
                    if response.status_code != 502:
                        break
                    body = response.json()
                    error = body.get("detail", "")
                    row["errors"].append(error)
                    save()
                    case_id = body.get("case_id") or case_id
                    if "TPD" in error or "HTTP 429" not in error:
                        break
                    delay = re.search(r"retry after ([\d.]+) seconds", error)
                    seconds = min(float(delay[1]) + 10 if delay else 45, 60)
                    print(
                        f"{scenario.name}: provider quota; retrying saved work in {seconds:.0f}s",
                        flush=True,
                    )
                    time.sleep(seconds)
                    response = (
                        client.post(f"/cases/{case_id}/investigate")
                        if case_id
                        else client.post(path, json=payload)
                    )
                return response

            try:
                if case_id:
                    stored = client.get(f"/cases/{case_id}").json()
                    response = (
                        client.get(f"/cases/{case_id}")
                        if stored["status"] in {"needs_input", "assessment_ready"}
                        else post(f"/cases/{case_id}/investigate")
                    )
                else:
                    response = post("/ask", {"summary": scenario.ticket})
                if response.status_code != 200:
                    raise AssertionError(response.text)
                body = response.json()
                case_id = body.get("case_id") or case_id
                result = body.get("result", body)
                while result["status"] == "needs_input":
                    snapshot = client.portal.call(
                        client.app.state.agent.graph.aget_state,
                        client.app.state.agent.config(case_id or body["request_id"]),
                    )
                    question, field = result["pending_question"], snapshot.values["pending_field"]
                    row["questions"].append({"question": question, "field": field})
                    assert len(row["questions"]) <= 3, "Too many human questions"
                    assert scenario.number == 1 or not re.search(
                        r"\b(floor|site|location)\b", question, re.I
                    ), "Irrelevant location question"
                    answers = {q[0]: q[4] for q in scenario.questions}
                    answers.update(
                        {
                            "paper_jam_cleared": "Physical jam clearance is unconfirmed; inspect the printer before resuming the queue.",
                            "printer_jam_cleared": "Physical jam clearance is unconfirmed; the queue is currently paused.",
                            "queue_status": "Paused",
                            "mfa_error": "The replacement phone cannot complete MFA; the old phone is registered.",
                            "identity_verified": "No; the technician must verify identity under the approved procedure.",
                        }
                    )
                    assert (
                        field in answers
                    ), f"Human question needs a fixture answer: {field}: {question}"
                    row["questions"][-1]["answer"] = answers[field]
                    save()
                    response = post(f"/cases/{case_id}/resume", {"answer": answers[field]})
                    assert response.status_code == 200, response.text
                    result = response.json()
                row["result"] = result
                assert result["status"] == "assessment_ready", result["uncertainty"]
                assert result["recommended_next_action"] and result["confidence"] > 0
                if args.mode == "azure_openai":
                    assert {"classify_intent", "plan", "reason", "verify"} <= {
                        s["stage"] for s in model_stages
                    }
                    assert all(s["provider"] == "azure_openai" for s in model_stages)
                    assert result["hypotheses"]
                    assert all(
                        h["status"] == "plausible" and h["evidence_ids"]
                        for h in result["hypotheses"]
                    )
                    assert any(e["source_system"] == "local_fixture" for e in result["evidence"])
                    assert "search_knowledge" in {t["name"] for t in tools}
                else:
                    public = [
                        e
                        for e in result["evidence"]
                        if e["tool"] == "search_knowledge"
                        and e["source_type"] == "public_documentation"
                        and "AADSTS53003" in str(e["content"])
                    ]
                    assert public and all(e["url"] and e["doc_id"] for e in public)
                    assert {"search_web", "fetch_web_page", "ingest_text"} <= {
                        t["name"] for t in tools
                    }
                    row["public_sources"] = [e["url"] for e in public]
                row["status"] = "PASS"
            except Exception as exc:
                row["status"] = (
                    "UNAVAILABLE"
                    if any("TPD" in e or "HTTP 429" in e for e in row["errors"])
                    else "FAIL"
                )
                row["errors"].append(str(exc))
            finally:
                row["case_id"] = case_id
                client.app.state.mcp.call_tool = original_protocol
                client.app.state.agent.provider.generate_structured = original_model
                save()
                print(
                    f"{scenario.name}: {row['status']}; tools={sorted({t['name'] for t in tools})}; questions={len(row['questions'])}; errors={row['errors']}",
                    flush=True,
                )
    print(f"Report: {args.report}; runtime: {root}", flush=True)


if __name__ == "__main__":
    main()
