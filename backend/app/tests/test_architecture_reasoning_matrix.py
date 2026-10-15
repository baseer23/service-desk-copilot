"""Ten required investigations through real LangGraph/LangChain/MCP/SQLite/Chroma.

Only the structured model is deterministic. These prove orchestration and evidence
safety, not live LLM reasoning quality. Every fixture stays in pytest temp stores.
Set REASONING_MATRIX_REPORT to write measured observations as private JSON.
"""

import json
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest
from backend.app.agent.models import VerificationResult
from backend.app.tests.scenario_data import SCENARIOS, Scenario, call, kb
from backend.app.tests.test_service_desk_scenarios import ScenarioProvider, runtime


MATRIX = [
    replace(SCENARIOS[1], number=1, name="Microsoft 365 MFA authentication failure"),
    replace(SCENARIOS[0], number=2, name="VPN authentication failure"),
    replace(SCENARIOS[2], number=3, name="Network printer unavailable"),
    replace(SCENARIOS[4], number=4, name="Active Directory account lockout"),
    replace(SCENARIOS[5], number=5, name="Outlook connectivity failure"),
    Scenario(6, "Multiple users reporting internet outages", "Several users at the same office cannot reach any internet sites. Internal file shares still work.",
        {"issue_type": "shared internet outage", "product": "Internet", "symptoms": "Several users cannot reach internet; internal shares available", "affected_scope": "Several users at one office", "entities": ["Internet"]},
        [kb("Internet outage gateway DNS internal shares"), call("get_active_alerts", company="B99", product="Internet")],
        "Isolated test procedure: compare gateway reachability, DNS resolution and upstream provider status when several users lose internet but internal file shares work. Gather error timestamps and network diagnostics before declaring the upstream provider responsible.",
        "A shared gateway, resolver or upstream dependency is plausible, not yet confirmed.",
        "Compare affected users' gateway reachability and DNS results, check upstream status and retain timestamps before escalating.", ("gateway", "dns")),
    replace(SCENARIOS[7], number=7, name="Application access denied"),
    Scenario(8, "Corporate Wi-Fi connection failure", "A corporate laptop cannot authenticate to the staff Wi-Fi while other devices connect normally.",
        {"issue_type": "wireless authentication", "product": "Wi-Fi", "symptoms": "One laptop cannot authenticate to staff Wi-Fi", "affected_scope": "One laptop", "entities": ["Wi-Fi"]},
        [kb("Wi-Fi authentication certificate profile RADIUS"), call("search_graph", entities=["Wi-Fi"], limit=5)],
        "Isolated wireless test procedure: gather the exact authentication error and inspect the approved Wi-Fi profile, certificate expiry and RADIUS evidence. Do not disable certificate validation or remove corporate security controls.",
        "A certificate or Wi-Fi profile issue is plausible, not yet confirmed.",
        "Collect the exact Wi-Fi authentication error and inspect certificate validity, approved profile settings and RADIUS events.", ("certificate", "error")),
    Scenario(9, "Contradictory knowledge-base troubleshooting instructions", "Two VPN knowledge articles conflict: one says disable certificate validation and another says certificate validation is mandatory. What should I do?",
        {"issue_type": "contradictory security instructions", "product": "VPN", "symptoms": "Knowledge articles disagree about certificate validation", "entities": ["VPN", "certificate"]},
        [kb("VPN certificate validation mandatory disable")],
        "VPN approved security procedure: certificate validation is mandatory. Verify the current policy owner and approval date before changing security settings.",
        "The conflicting articles require policy-owner validation; a safe fix is not yet confirmed.",
        "Preserve certificate validation and ask the policy owner to reconcile the approval dates and applicability of the conflicting articles before applying changes.", ("policy owner", "conflicting")),
    Scenario(10, "Incident with incomplete information and no matching knowledge", "Something is broken. Please fix it.",
        {"issue_type": "unspecified incident", "symptoms": "No system, error, scope or identifier supplied", "entities": [], "missing_information": ["affected system", "exact error"]},
        [kb("unknown incident missing exact error")], "", "Cause unknown without diagnostics.", "Collect the affected application and exact error.", ("error",),
        questions=(("affected_application", "Which application or device is affected, and what exact error appears?", "requester", "The affected system and error are required before selecting safe diagnostics.", "Corporate Wi-Fi"),)),
]
OBSERVATIONS = []


class EvaluationProvider(ScenarioProvider):
    def __init__(self, scenario):
        super().__init__(scenario)
        self.verifications = 0
        self.rejected_claims = []

    async def generate_structured(self, stage, context, schema):
        result = await super().generate_structured(stage, context, schema)
        if stage == "verify":
            self.verifications += 1
            if self.scenario.number == 9:
                conflict = "Conflicting VPN articles recommend disabling versus requiring certificate validation; approval and applicability remain unverified."
                if self.verifications == 1:
                    result = VerificationResult(
                        unsupported_claims=["Disabling certificate validation is a verified fix"],
                        contradictions=[conflict], missing_evidence=["Current policy owner's decision"],
                        evidence_ids=result.evidence_ids, confidence=0,
                        should_continue_research=True, justification="Reject unsafe unsupported fix and require a revised assessment.",
                    )
                    self.rejected_claims.extend(result.unsupported_claims)
                else:
                    result = result.model_copy(update={"contradictions": [conflict], "should_continue_research": False})
        return result


def measured_report(path):
    report = {
        "execution": "Actual LangGraph, StructuredTool, MCP stdio, temporary SQLite and Chroma; explicit deterministic structured model fixtures only",
        "live_model_quality_verified": False,
        "production_data_written": False,
        "scenarios": OBSERVATIONS,
    }
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w") as output:
        json.dump(report, output, indent=2)
    destination.chmod(0o600)


@pytest.mark.parametrize("scenario", MATRIX, ids=[s.name for s in MATRIX])
def test_required_reasoning_matrix(scenario, tmp_path, monkeypatch):
    # Use the actual SDK transport, with no fake HTTP tools and no external calls.
    with runtime(tmp_path, monkeypatch, scenario=None, fake_network=False) as (client, protocol, structured):
        agent = client.app.state.agent
        provider = EvaluationProvider(scenario)
        agent.provider = provider
        if scenario.kb:
            client.portal.call(agent.harness.invoke, "ingest_text", {
                "title": "Isolated evaluation KB", "text": scenario.kb,
                "source": "pytest isolated evaluation", "source_type": "local_fixture",
            })
        if scenario.number == 9:
            client.portal.call(agent.harness.invoke, "ingest_text", {
                "title": "Conflicting historical evaluation KB", "source": "pytest isolated historical source",
                "source_type": "local_fixture",
                "text": "Historical VPN article recommends disable certificate validation when authentication fails. Its approval owner and current applicability are unknown.",
            })
        start_index, start = len(protocol), time.perf_counter()
        response = client.post("/ask", json={"summary": scenario.ticket})
        assert response.status_code == 200, response.text
        request = response.json()
        result = request["result"]
        questions = []
        if scenario.number != 10:
            while result["status"] == "needs_input":
                assert len(questions) < len(scenario.questions)
                snapshot = client.portal.call(agent.graph.aget_state, agent.config(request["case_id"] or request["request_id"]))
                spec = next(q for q in scenario.questions if q[0] == snapshot.values["pending_field"])
                questions.append(result["pending_question"])
                resumed = client.post(f"/cases/{request['case_id']}/resume", json={"answer": spec[4]})
                assert resumed.status_code == 200, resumed.text
                result = resumed.json()
        elapsed = time.perf_counter() - start
        evidence = {row["id"]: row for row in result["evidence"]}
        diagnostic = [row for row in evidence.values() if row["tool"] not in {"user_request", "search_memory"} and row["source_type"] != "tool_error" and row["content"].get("available") is not False]
        events = client.get(f"/cases/{request['case_id']}/events").json() if request.get("case_id") else request["events"]
        tools = [name for name, _ in protocol[start_index:]]
        assert protocol == structured, "A capability bypassed the LangChain/MCP path"
        assert all(stage in provider.stages for stage in ["classify_intent", "analyze_ticket", "plan", "reason", "verify"])
        assert "search_knowledge" in tools
        assert result["status"] != "resolved"
        assert all(row["source"] and row["timestamp"] and row["source_system"] for row in evidence.values())
        assert result["research_iteration"] <= agent.settings.max_investigation_cycles
        for hypothesis in result.get("hypotheses", []):
            assert hypothesis["evidence_ids"] and set(hypothesis["evidence_ids"]) <= evidence.keys()
        if scenario.number == 10:
            assert result["status"] in {"needs_input", "insufficient_information"}
            assert result["confidence"] == 0
            assert not diagnostic, "No-source scenario unexpectedly received diagnostic evidence"
            assert not result.get("final_answer") and not result.get("inferences")
            assert result.get("pending_question") or result.get("missing_information")
        else:
            assert result["status"] == "assessment_ready", result
            assert diagnostic and any(row["tool"] == "search_knowledge" for row in diagnostic)
            assert result["confidence"] < .9
            assert all(term in result["recommended_next_action"].lower() for term in scenario.action_terms)
            assert result["uncertainty"] and result["hypotheses"]
        if scenario.number == 9:
            assert provider.verifications >= 2
            assert result["research_iteration"] >= 2
            assert result["contradictions"]
            assert "Disabling certificate validation is a verified fix" not in str(result.get("final_answer"))
            assert "policy owner" in result["recommended_next_action"]
        if scenario.number == 8:
            assert "search_graph" in tools, "Available graph relationships were not considered"
        observation = {
            "scenario": scenario.name, "test_outcome": "passed",
            "investigation_stages_executed": list(dict.fromkeys([event["tool"] for event in events] + provider.stages)),
            "evidence_retrieved": [{"tool": row["tool"], "source": row["source"], "source_type": row["source_type"], "id": row["id"]} for row in diagnostic],
            "mcp_tools_invoked": list(dict.fromkeys(tools)),
            "model_stages": provider.stages,
            "replanning_iterations": result["research_iteration"],
            "unsupported_claims_detected": provider.rejected_claims,
            "unsupported_final_claims": 0,
            "final_status": result["status"],
            "final_recommendation": result.get("recommended_next_action"),
            "final_recommendation_quality": "Required clarification; no invented diagnosis" if scenario.number == 10 else "Evidence-referenced diagnostic recommendation; resolution remains unverified",
            "llm_token_usage": None, "estimated_api_cost_cad": None,
            "usage_note": "Model fixture: no inference executed, so token/cost measurements are unavailable",
            "execution_duration_seconds": round(elapsed, 4),
            "live_neo4j_verified": False,
        }
        OBSERVATIONS.append(observation)
        if path := os.environ.get("REASONING_MATRIX_REPORT"):
            measured_report(path)
