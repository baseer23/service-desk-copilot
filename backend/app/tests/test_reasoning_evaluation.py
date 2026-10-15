"""Offline checks of report honesty; these never dispatch real inference."""

import json
import os
import subprocess
import sys

from scripts.evaluate_reasoning import EVALUATION_METRICS, SCENARIOS, summarize, write_private


def test_report_does_not_claim_unexecuted_verifier_or_known_root_cause(settings):
    config = settings.model_copy(
        update={"groq_input_cad_per_million": None, "groq_output_cad_per_million": None}
    )
    state = {
        "status": "needs_input",
        "pending_question": "What exact error appears?",
        "evidence": [],
        "uncertainty": ["No diagnostic evidence"],
    }
    activity = [{"kind": "workflow", "stage": "plan", "status": "running"}]
    result = summarize(
        SCENARIOS[0],
        "groq",
        state,
        activity,
        [{"tool": "search_knowledge", "status": "completed", "returned_items": 0}],
        1,
        config,
    )
    assert result["unsupported_claims_detected"] is None
    assert result["checks"]["no_unsupported_verified_claims"] is None
    assert result["estimated_api_cost_cad"] is None
    assert result["evidence_retrieved"]["diagnostic"] == 0
    assert "no verified resolution" in result["recommendation_quality"]


def test_unverified_claim_and_fixture_evidence_require_review(settings):
    config = settings.model_copy(
        update={"groq_input_cad_per_million": 1, "groq_output_cad_per_million": 2}
    )
    state = {
        "status": "assessment_ready",
        "verification": {"unsupported_claims": ["Invented cause"]},
        "evidence": [{"source_type": "system_record", "source_system": "local_fixture"}],
    }
    activity = [
        {"kind": "workflow", "stage": "verify", "status": "running"},
        {
            "kind": "llm_usage",
            "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        },
    ]
    result = summarize(
        SCENARIOS[0], "groq", state, activity, [{"tool": "search_knowledge"}], 1, config
    )
    assert result["outcome"] == "review_required"
    assert result["checks"]["no_fixture_evidence"] is False
    assert result["unsupported_claims_detected"] == ["Invented cause"]
    assert result["estimated_api_cost_cad"] == 0.0002


def test_private_report_permissions_and_explicit_live_opt_in(tmp_path):
    output = tmp_path / "report.json"
    write_private(output, {"real_inference": False})
    assert output.stat().st_mode & 0o777 == 0o600
    assert json.loads(output.read_text()) == {"real_inference": False}
    result = subprocess.run(  # noqa: S603 - Fixed local script and isolated output path.
        [
            sys.executable,
            "scripts/evaluate_reasoning.py",
            "--provider",
            "groq",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": "."},
    )
    assert result.returncode == 2
    assert "Real inference requires --live" in result.stderr
    assert json.loads(output.read_text()) == {"real_inference": False}


def test_at_least_fifteen_diverse_scenarios_and_explicit_metrics_are_present():
    assert len(SCENARIOS) >= 15
    assert len({row[0] for row in SCENARIOS}) == len(SCENARIOS)
    assert any(row[2] == "my vpn isnt working" for row in SCENARIOS)
    assert set(EVALUATION_METRICS) == {
        "understanding", "clarification", "tool_selection", "search_quality",
        "evidence_quality", "reasoning_quality", "citation_accuracy", "efficiency",
        "resilience", "explainability",
    }
