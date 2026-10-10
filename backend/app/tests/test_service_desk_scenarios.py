"""Versatility validation across real LangGraph→StructuredTool→MCP execution.

Only structured model responses and external DNS/HTTP/search are deterministic
fixtures. SQLite, Chroma, MCP SDK transport, repositories and graph are real.
Run with -vv -s; SCENARIO_REPORT optionally writes the actual observations.
"""

import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.tools import StructuredTool

from backend.app.agent.models import (
    InvestigationDecision,
    InvestigationPlan,
    RequestIntent,
    ResearchSelection,
    TicketAnalysis,
    VerificationResult,
)
from backend.app.core.config import Settings
from backend.app.mcp.client import DeskMateMCPClient
from backend.app.tests.scenario_data import SCENARIOS, MICROSOFT_URL, PUBLIC_HTML

REPORTS = []
INFRASTRUCTURE = {
    "save_case",
    "save_case_note",
    "get_case",
    "get_case_history",
    "list_cases",
    "remember_fact",
    "list_available_context",
}


class ScenarioProvider:
    """Generic canned-model interpreter; deliberately absent from runtime code."""

    def __init__(self, scenario):
        self.scenario = scenario
        self.stages = []

    def name(self):
        return "scenario_model_fixture"

    @staticmethod
    def facts(context):
        return {
            f["predicate"]: f["value"]
            for f in (context.get("remembered_facts") or [])
            + (context.get("confirmed_facts") or [])
        }

    async def generate_structured(self, stage, context, schema):
        self.stages.append(stage)
        scenario = self.scenario
        if stage == "classify_intent":
            return RequestIntent(
                workflow=scenario.workflow,
                goal=scenario.ticket,
                justification="Canned classification fixture for this validation input",
            )
        if stage == "analyze_ticket":
            return TicketAnalysis(**scenario.analysis)
        if stage == "plan":
            facts = self.facts(context)
            calls = []
            for template in scenario.calls:
                arguments = dict(template["arguments"])
                valid = True
                for key, value in arguments.items():
                    if isinstance(value, str) and value.startswith("$"):
                        value = facts.get(value[1:])
                        if value is None:
                            valid = False
                        arguments[key] = value
                if valid:
                    calls.append({**template, "arguments": arguments})
            if facts.get("current_site"):
                calls.append(
                    {
                        "name": "get_site_context",
                        "arguments": {
                            "company": scenario.analysis["company"],
                            "site": facts["current_site"],
                        },
                        "reason": "Human location establishes site applicability",
                    }
                )
            return InvestigationPlan(
                goal="Gather category-relevant evidence",
                tool_calls=calls,
                parallel_groups=[list(range(len(calls)))],
                stop_condition="A verified next action or a material clarification is supported",
            )
        evidence = context.get("evidence") or []
        diagnostic = [
            e
            for e in evidence
            if e["tool"] not in {"user_request", "search_web", "search_memory"}
            and e["source_type"] != "tool_error"
            and e["content"].get("available") is not False
        ]
        ids = [e["id"] for e in diagnostic]
        if stage == "reason":
            facts = self.facts(context)
            question = next((q for q in scenario.questions if q[0] not in facts), None)
            public = [
                e
                for e in diagnostic
                if e["tool"] == "search_knowledge"
                and e["source_type"] == "public_documentation"
                and "AADSTS53003" in str(e["content"])
            ]
            research_needed = bool(scenario.research_query) and not public
            enough = bool(ids) and not research_needed and question is None
            return InvestigationDecision(
                hypotheses=[
                    {
                        "claim": scenario.hypothesis,
                        "evidence_ids": ids,
                        "confidence": 0.65,
                        "status": "plausible",
                    }
                ]
                if ids and not research_needed
                else [],
                inferences=[scenario.hypothesis] if ids and not research_needed else [],
                evidence_summary="Current records and historical KB provide a next diagnostic action; causation remains unconfirmed.",
                missing_information=[{"description": question[3]}]
                if question
                else (
                    [{"description": "The public error definition is absent internally"}]
                    if research_needed
                    else []
                ),
                question_for_human=question[1] if question else None,
                human_field=question[0] if question else None,
                question_subject=question[2] if question else None,
                question_reason=question[3] if question else None,
                needs_web_research=research_needed,
                research_queries=[scenario.research_query] if research_needed else [],
                preferred_domains=["learn.microsoft.com"] if research_needed else [],
                enough_information=enough,
                likely_issue=scenario.hypothesis if enough else None,
                answer="Microsoft documentation defines AADSTS53003 as BlockedByConditionalAccess; review sign-in logs to establish the tenant policy involved."
                if public
                else None,
                recommended_next_action=scenario.action if enough else None,
                troubleshooting_steps=[scenario.action] if enough else [],
                confidence=0.65 if enough else 0.3,
                uncertainty=["No diagnostic evidence proves a root cause or completed resolution."],
                decision_justification="Use the observed evidence to establish the next step, while retaining uncertainty and any stale-memory discrepancy.",
            )
        if stage == "verify":
            d = context["decision"]
            return VerificationResult(
                supported_claims=[d["evidence_summary"]] if ids else [],
                evidence_ids=ids,
                confidence=d["confidence"],
                should_continue_research=False,
                justification="Canned independent verification response: plausible hypotheses remain separate from facts; a material question is permitted; freshness conflicts are retained.",
            )
        if stage == "evaluate_sources":
            rows = context["results"]
            selected = [
                r["url"]
                for r in rows
                if "learn.microsoft.com" in r["url"]
                and ("text" not in r or "AADSTS53003" in r["text"])
            ][:1]
            return ResearchSelection(
                selected_urls=selected,
                evidence_gap="Public error definition",
                justification="Select a supplied authoritative URL only when its fetched content addresses the reported error",
            )
        raise AssertionError(stage)


def network_wrapper(path, fail_fetch=False):
    """Patch network adapters in the child process, preserving every MCP tool."""
    script = f"""#!{sys.executable}
import socket
import requests
import ddgs
from types import SimpleNamespace
from backend.app.mcp import server
from backend.app.services import web

class FakeSearch:
    def __init__(self, **kwargs): pass
    def text(self, query, **kwargs):
        return [{{"title": "Microsoft identity platform error codes", "href": {MICROSOFT_URL!r}, "body": "Microsoft public error reference for AADSTS53003"}}]
class FakeSession:
    def get(self, url, **kwargs):
        if {fail_fetch!r}: raise requests.ConnectionError("Fictional network unavailable")
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response._content = {PUBLIC_HTML.encode()!r}
        response._content_consumed = True
        return response
ddgs.DDGS = FakeSearch
web.socket.getaddrinfo = lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
web.requests = SimpleNamespace(Session=FakeSession)
server.server.run(transport="stdio")
"""
    path.write_text(script)
    path.chmod(0o700)
    return str(path)


@contextmanager
def runtime(tmp_path, monkeypatch, scenario=None, *, fake_network=True, fail_fetch=False):
    import backend.app.main as main

    settings = Settings(
        _env_file=None,
        test_mode=True,
        generation_provider="stub",
        embed_provider="stub",
        neo4j_uri="",
        chroma_dir=tmp_path / "chroma",
        case_db_path=tmp_path / "cases.db",
        url_rate_limit_sec=0,
    )
    command = (
        network_wrapper(tmp_path / "mcp-network-fixture", fail_fetch) if fake_network else None
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            main,
            "DeskMateMCPClient",
            lambda settings: DeskMateMCPClient(settings, server_command=command),
        )
        with TestClient(main.create_app(settings)) as client:
            if scenario:
                client.app.state.agent.provider = ScenarioProvider(scenario)
                ingest = client.portal.call(
                    client.app.state.agent.harness.invoke,
                    "ingest_text",
                    {
                        "title": f"Fictional internal KB: {scenario.name}",
                        "text": scenario.kb,
                        "source": "Fictional service-desk validation KB",
                        "source_type": "local_fixture",
                    },
                )
                assert ingest["status"] == "ok"
            protocol, structured = [], []
            original_protocol = client.app.state.mcp.call_tool
            original_structured = StructuredTool.ainvoke

            async def trace_protocol(name, arguments):
                protocol.append((name, arguments))
                return await original_protocol(name, arguments)

            async def trace_structured(tool, arguments, *args, **kwargs):
                structured.append(
                    (tool.name, tool.args_schema.model_validate(arguments).model_dump())
                )
                return await original_structured(tool, arguments, *args, **kwargs)

            patch.setattr(client.app.state.mcp, "call_tool", trace_protocol)
            patch.setattr(StructuredTool, "ainvoke", trace_structured)
            yield client, protocol, structured


def investigate(client, scenario):
    response = client.post("/ask", json={"summary": scenario.ticket})
    assert response.status_code == 200, response.text
    request = response.json()
    result = request["result"]
    questions = []
    while result["status"] == "needs_input":
        assert len(questions) < len(scenario.questions), result
        snapshot = client.portal.call(
            client.app.state.agent.graph.aget_state,
            client.app.state.agent.config(request["case_id"] or request["request_id"]),
        )
        field = snapshot.values["pending_field"]
        specification = next(q for q in scenario.questions if q[0] == field)
        question = result["pending_question"]
        assert question and specification[3]
        questions.append({"field": field, "question": question, "answer": specification[4]})
        case_id = request["case_id"]
        if case_id:
            resumed = client.post(f"/cases/{case_id}/resume", json={"answer": specification[4]})
            assert resumed.status_code == 200, resumed.text
            result = resumed.json()
        else:
            resumed = client.post(
                f"/requests/{request['request_id']}/resume", json={"answer": specification[4]}
            )
            assert resumed.status_code == 200, resumed.text
            request = resumed.json()
            result = request["result"]
    events = (
        client.get(f"/cases/{request['case_id']}/events").json()
        if request["case_id"]
        else request["events"]
    )
    return request, result, questions, events


def assert_assessment(scenario, result):
    assert result["status"] == "assessment_ready", result
    assert result["hypotheses"] and result["inferences"]
    assert 0 < result["confidence"] < 0.9
    assert all(
        h["status"] == "plausible" and "not yet confirmed" in h["claim"]
        for h in result["hypotheses"]
    )
    evidence = {e["id"]: e for e in result["evidence"]}
    assert all(
        h["evidence_ids"] and set(h["evidence_ids"]) <= evidence.keys()
        for h in result["hypotheses"]
    )
    assert all(term in result["recommended_next_action"].lower() for term in scenario.action_terms)
    for e in evidence.values():
        assert e["source_system"] and e["source"] and e["timestamp"]
        if e["tool"] in {
            "get_user_context",
            "get_device_context",
            "get_site_context",
            "get_company_context",
            "search_previous_tickets",
            "get_active_alerts",
            "get_recent_changes",
        }:
            assert e["source_system"] == e["content"]["source_system"] == "local_fixture"
        if e["source_type"] == "document":
            assert e["source_system"] == "local_fixture"
    for fact in result["confirmed_facts"]:
        assert fact["source"] in {"human_confirmed", "trusted_tool", "resolved_outcome"}
        assert fact["value"] not in result["inferences"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
def test_service_desk_scenario(scenario, tmp_path, monkeypatch):
    with runtime(tmp_path, monkeypatch, scenario) as (client, protocol, structured):
        if scenario.number == 8:
            older = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
            client.portal.call(
                client.app.state.agent.harness.invoke,
                "remember_fact",
                {
                    "fact": {
                        "subject": "Maria",
                        "predicate": "works_at",
                        "value": "Fabrikam",
                        "source": "trusted_tool",
                        "source_system": "local_fixture",
                        "observed_at": older,
                        "confidence": 0.8,
                        "case_id": "fictional-historical-case",
                    }
                },
            )
        start = len(protocol)
        request, result, questions, events = investigate(client, scenario)
        assert_assessment(scenario, result)
        assert protocol == structured, "A capability bypassed StructuredTool or MCP"
        assert request["intent"] == (
            "knowledge_question" if scenario.workflow == "knowledge" else "service_desk"
        )
        assert [q["field"] for q in questions] == [q[0] for q in scenario.questions]
        if scenario.number in {2, 5, 6, 8}:
            assert not any(
                re.search(r"\b(floor|site|location)\b", q["question"], re.I) for q in questions
            )
        if scenario.number == 8:
            assert any(
                "Fabrikam" in c and "Northwind" in c and "freshness" in c
                for c in result["contradictions"]
            )
        if scenario.number == 4:
            tools = [e["tool"] for e in events]
            assert "get_device_context" not in tools
            assert tools.index("get_active_alerts") < tools.index("finalize")
        if scenario.number == 10:
            calls = [n for n, _ in protocol[start:]]
            assert (
                calls.index("search_knowledge")
                < calls.index("search_web")
                < calls.index("fetch_web_page")
                < calls.index("ingest_text")
            )
            assert calls.count("search_knowledge") >= 2
            public = [
                e
                for e in result["evidence"]
                if e["tool"] == "search_knowledge" and e["source_type"] == "public_documentation"
            ]
            assert any(
                e["url"] == MICROSOFT_URL and e["doc_id"] and "AADSTS53003" in e["content"]["text"]
                for e in public
            )
            assert MICROSOFT_URL in result["final_response"]["sources"]
        row = {
            "number": scenario.number,
            "scenario_name": scenario.name,
            "input_ticket": scenario.ticket,
            "classified_intent": request["intent"],
            "tools_called": list(dict.fromkeys(n for n, _ in protocol[start:])),
            "human_questions": questions,
            "evidence_sources": list(dict.fromkeys(e["source"] for e in result["evidence"])),
            "hypotheses": result["hypotheses"],
            "recommended_next_action": result["recommended_next_action"],
            "final_status": result["status"],
            "confidence": result["confidence"],
            "errors": [
                e["content"] for e in result["evidence"] if e["source_type"] == "tool_error"
            ],
            "contradictions": result["contradictions"],
            "chroma_used": any(n == "search_knowledge" for n, _ in protocol[start:]),
            "graph_connected": any(
                e["tool"] == "search_graph" and e["content"].get("available") is not False
                for e in result["evidence"]
            ),
            "memory_used": bool(result["remembered_facts"]),
            "pass": True,
        }
        REPORTS.append(row)
        print(
            f"\n{scenario.number:2} {scenario.name}: {request['intent']} | {', '.join(n for n in row['tools_called'] if n not in INFRASTRUCTURE)} | human={len(questions)} | {result['status']} PASS"
        )
        if scenario.number == 1:
            followup, reused, repeat_questions, _ = investigate(client, scenario)
            assert_assessment(scenario, reused)
            assert not repeat_questions and len(reused["remembered_facts"]) >= 2
            assert followup["case_id"] != request["case_id"]
            row["memory_used"] = True


def resume_pdf():
    # Minimal real PDF extracted by pypdf in the MCP process, not a parser mock.
    stream = b"BT /F1 12 Tf 40 700 Td (Fictional resume fixture: Abdul Khan graduated in 2022. Skills: Python, React, SQL.) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    data = b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n"
    data += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets[1:])
    return data + f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()


def test_resume_knowledge_creates_no_case(tmp_path, monkeypatch):
    with runtime(tmp_path, monkeypatch) as (client, protocol, structured):
        response = client.post(
            "/ingest/pdf",
            files={"file": ("fictional-resume.pdf", resume_pdf(), "application/pdf")},
            data={"title": "Fictional Abdul resume fixture"},
        )
        assert response.status_code == 200, response.text
        protocol.clear()
        structured.clear()
        response = client.post("/ask", json={"summary": "What year did Abdul graduate?"})
        assert response.status_code == 200, response.text
        request = response.json()
        assert request["intent"] == "knowledge_question" and request["case_id"] is None
        assert request["result"]["status"] == "assessment_ready"
        assert "2022" in request["result"]["final_response"]["answer"]
        assert not request["result"]["pending_question"]
        tools = {name for name, _ in protocol}
        assert "search_knowledge" in tools
        assert (
            not {
                "save_case",
                "save_case_note",
                "get_user_context",
                "get_site_context",
                "get_device_context",
            }
            & tools
        )
        assert all(e["case_id"] is None for e in request["result"]["evidence"])
        assert protocol == structured
        assert client.get("/cases").json() == []


def test_unknown_internal_ip_does_not_hallucinate(tmp_path, monkeypatch):
    with runtime(tmp_path, monkeypatch) as (client, protocol, structured):
        # The ordinary stub has only legacy ticket/document classifications.
        # Supply this unsupported knowledge intent as a canned model response.
        provider = client.app.state.agent.provider
        original = provider.generate_structured

        async def unknown(stage, context, schema):
            if stage == "classify_intent":
                return RequestIntent(
                    workflow="knowledge",
                    goal=context["request"],
                    justification="A private infrastructure fact requires an authoritative source",
                )
            if stage == "reason":
                return InvestigationDecision(
                    evidence_summary="No indexed source contains the requested internal address.",
                    enough_information=False,
                    confidence=0,
                    uncertainty=["Insufficient evidence: the private IP is unknown."],
                    recommended_next_action="Consult the authorized internal asset inventory.",
                )
            return await original(stage, context, schema)

        monkeypatch.setattr(provider, "generate_structured", unknown)
        response = client.post(
            "/ask", json={"summary": "What is the production domain controller IP for Contoso?"}
        )
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result["workflow"] == "knowledge"
        assert result["status"] == "insufficient_information" and result["confidence"] == 0
        assert (
            result["final_response"]["answer"] is None
            and not result["final_response"]["sources"][1:]
        )
        assert any(
            "evidence" in u.lower() or "information" in u.lower() for u in result["uncertainty"]
        )
        assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", json.dumps(result))
        assert all(e["tool"] == "user_request" for e in result["evidence"])
        assert protocol == structured
        assert client.get("/cases").json() == []


def test_failed_web_fetch_retains_insufficiency_without_answer(tmp_path, monkeypatch):
    with runtime(tmp_path, monkeypatch, SCENARIOS[9], fail_fetch=True) as (
        client,
        protocol,
        structured,
    ):
        response = client.post("/ask", json={"summary": SCENARIOS[9].ticket})
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result["status"] == "insufficient_information" and result["confidence"] == 0
        assert result["final_response"]["answer"] is None
        assert any(
            "fetch_web_page unavailable" in uncertainty for uncertainty in result["uncertainty"]
        )
        assert any(
            e["tool"] == "fetch_web_page" and e["source_type"] == "tool_error"
            for e in result["evidence"]
        )
        assert not any(
            e["source_type"] == "public_documentation" and e["doc_id"] for e in result["evidence"]
        )
        assert protocol == structured


def test_versatility_matrix():
    assert len(REPORTS) == 10
    diagnostic_sets = {frozenset(r["tools_called"]) - INFRASTRUCTURE for r in REPORTS}
    assert len(diagnostic_sets) >= 6, "Scenarios blindly used one tool list"
    tools = sorted({n for r in REPORTS for n in r["tools_called"]})
    assert {
        "get_device_context",
        "get_user_context",
        "get_company_context",
        "get_active_alerts",
        "get_recent_changes",
        "search_previous_tickets",
        "search_knowledge",
        "search_web",
        "fetch_web_page",
        "ingest_text",
        "remember_fact",
        "search_memory",
    } <= set(tools)
    report = {
        "mode": "Real graph/StructuredTool/MCP/SQLite/Chroma; canned model and external network only",
        "scenarios": REPORTS,
        "unique_mcp_tools": tools,
        "unique_diagnostic_plans": len(diagnostic_sets),
        "unnecessary_human_questions": 0,
        "human_interrupt_cases": sum(bool(r["human_questions"]) for r in REPORTS),
        "web_cases": sum("search_web" in r["tools_called"] for r in REPORTS),
        "memory_cases": sum(r["memory_used"] for r in REPORTS),
        "chroma_cases": sum(r["chroma_used"] for r in REPORTS),
        "neo4j_connected_cases": sum(r["graph_connected"] for r in REPORTS),
        "alerts_changes_cases": sum(
            bool({"get_active_alerts", "get_recent_changes"} & set(r["tools_called"]))
            for r in REPORTS
        ),
    }
    print("\nVERSATILITY MATRIX")
    for row in REPORTS:
        print(
            f"{row['number']:2} | {row['scenario_name']:20} | {row['classified_intent']:18} | human={len(row['human_questions'])} | {row['final_status']} | PASS"
        )
    print(f"Unique MCP tools ({len(tools)}): {', '.join(tools)}")
    if destination := os.environ.get("SCENARIO_REPORT"):
        Path(destination).write_text(json.dumps(report, indent=2))
