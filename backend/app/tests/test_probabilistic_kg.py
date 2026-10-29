"""Offline probabilistic knowledge-graph experiment (dev_exp).

One end-to-end comparison of approaches A (dev's semantic pipeline), B
(probabilistic reasoning, validated, Python-driver writes) and C (the same
plan through a write-enabled neo4j-mcp stand-in over real MCP stdio), plus the
guarantees the comparison depends on. Model output is recorded; no Azure,
Groq or Neo4j Aura request is made.
"""

import asyncio
import sys
from types import SimpleNamespace

import pytest
from backend.app.experiments.probabilistic_kg import pipelines
from backend.app.experiments.probabilistic_kg.evaluate import evaluate
from backend.app.experiments.probabilistic_kg.models import ProbabilisticGraphProposal
from backend.app.experiments.probabilistic_kg.mutations import (
    FORBIDDEN_CLAUSES,
    TEMPLATES,
    Statement,
    compile_plan,
)
from backend.app.experiments.probabilistic_kg.recorded import proposal_b, resolve_ref
from backend.app.experiments.probabilistic_kg.retrieval import Fact, GraphView, Retriever
from backend.app.experiments.probabilistic_kg.validation import ProposalValidator
from backend.app.experiments.probabilistic_kg.writers import (
    McpGraphWriter,
    UnauthorizedStatement,
    build_writer_env,
    restricted_writer_client,
)
from backend.app.mcp.client import MCPConnectionError

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def index():
    return pipelines.build_index()


@pytest.fixture(scope="module")
def results():
    return asyncio.run(evaluate())


def test_end_to_end_comparison_reports_measured_differences(results):
    a, b, c = (results["approaches"][key] for key in "ABC")
    # A runs dev_exp's production pipeline, which now grounds entities and keeps
    # claim statuses, so it matches B on unsupported facts for the same reading.
    assert b["graph"]["hallucinated_facts"] <= a["graph"]["hallucinated_facts"]
    # What still differs: conflicts, element-exact citations, cross-document links.
    assert b["graph"]["contradictions_detected"] > a["graph"]["contradictions_detected"]
    assert b["graph"]["attribution_strict"] > a["graph"]["attribution_strict"]
    assert b["graph"]["duplicate_entities"] < a["graph"]["duplicate_entities"]
    # B's strictness costs one true fact whose citation was wrong.
    assert b["graph"]["true_facts_held_as_hypotheses"] == 1
    # Exact form and table answers are deterministic and identical everywhere.
    for approach in (a, b, c):
        assert all(row["correct"] and row["evidence_correct"]
                   for row in approach["retrieval"]["exact"])
    agentic = {row["id"]: row for row in b["retrieval"]["multi_hop"]["agentic"]}
    baseline = {row["id"]: row for row in a["retrieval"]["multi_hop"]["agentic"]}
    assert agentic["M4"]["answer"] == "CONFLICT"          # conflicting effective dates
    assert baseline["M4"]["answer"] != "CONFLICT"
    assert agentic["M3"]["correct"] and not baseline["M3"]["correct"]  # cross-document
    for approach in (agentic, baseline):
        assert approach["M6"]["answer"] == "NOT_ESTABLISHED"  # a hypothesis is not a fact
    # C wrote exactly what B wrote; only the transport differs.
    transport = results["transport"]
    assert transport["identical_graphs"]
    assert transport["B"]["ok"] and transport["C"]["ok"]
    assert transport["B"]["transactions"] == 2 and transport["B"]["atomic"]
    assert transport["C"]["transactions"] == transport["C"]["write_calls"]
    assert not transport["C"]["atomic"]
    assert b["graph"] == c["graph"]


def test_validation_never_upgrades_and_checks_form_answers(index):
    proposal = proposal_b(index, "form")
    proposal = proposal.model_copy(update={"relationships": [
        relation.model_copy(update={"confidence": 1.0}) for relation in proposal.relationships
    ]})
    graph = ProposalValidator(index).validate([proposal_b(index, "policy"), proposal])
    reasons = {(r.item_type, r.reason) for r in graph.rejections}
    assert ("entity", "name_not_in_cited_evidence") in reasons          # Warehouse Manager
    assert ("relationship", "contradicts_extracted_selection") in reasons  # misread "Yes"
    assert ("form_reading", "contradicts_extracted_selection") in reasons
    assert graph.form_readings_rejected == 1
    statuses = {claim.predicate: claim for claim in graph.claims}
    assert statuses["HAS_POSITION"].proposed_kind == "observed"
    assert statuses["HAS_POSITION"].status == "derived"  # confidence 1.0 did not keep "observed"
    for claim in graph.claims:
        order = ["hypothesis", "derived", "observed"]
        assert order.index(claim.status) <= order.index(claim.proposed_kind)


def test_cross_document_claim_must_cite_its_own_document(index):
    validator = ProposalValidator(index)
    graph = validator.validate([proposal_b(index, "policy")])
    foreign_only = ProbabilisticGraphProposal.model_validate({
        "document_id": "form",
        "entities": [{"id": "mc", "name": "Maria Chen", "entity_type": "Person",
                      "evidence_ids": [resolve_ref(index, "p:form-1")]}],
        "relationships": [{"subject_id": "mc", "predicate": "ELIGIBLE_FOR",
                           "object_id": "existing:benefit:name:group benefits",
                           "kind": "derived", "evidence_ids": [resolve_ref(index, "p:pol-2")]}],
    })
    graph = validator.validate([foreign_only], existing=graph)
    assert ("relationship", "unknown_evidence") in {(r.item_type, r.reason) for r in graph.rejections}


def test_compiled_plan_uses_only_non_destructive_templates(index):
    graph = ProposalValidator(index).validate(
        [proposal_b(index, document_id) for document_id in pipelines.INGESTION_ORDER]
    )
    plan = compile_plan(graph, index, "tenant-x")
    assert plan.statements
    for statement in plan.statements:
        assert TEMPLATES[statement.template] == statement.query
        assert not any(clause in statement.query.upper() for clause in FORBIDDEN_CLAUSES)
        assert all(str(row.get("id", "")).startswith(plan.tenant_hash)
                   for row in statement.params["rows"] if "id" in row)
    hypotheses = [s for s in plan.statements if s.template == "hypotheses"]
    related = [row for s in plan.statements if s.template == "related" for row in s.params["rows"]]
    assert hypotheses and all(row["status"] in {"observed", "derived"} for row in related)


def test_mcp_writer_refuses_any_text_but_an_allowlisted_template():
    calls = []

    class Client:
        async def call_tool(self, name, arguments):
            calls.append(name)
            return {"rows": [{"written": 1}]}

    plan = SimpleNamespace(tenant="t", expected={}, statements=[
        SimpleNamespace(template="entities", query="MATCH (n) DETACH DELETE n", params={}),
    ])
    with pytest.raises(UnauthorizedStatement):
        asyncio.run(McpGraphWriter(Client()).write(plan))
    assert calls == []
    with pytest.raises(KeyError):
        _ = Statement("model_written", {}).query  # no template, so no query text exists


def test_read_only_connection_cannot_write_and_failure_is_not_retried(index, tmp_path):
    graph = ProposalValidator(index).validate([proposal_b(index, "manual")])
    plan = compile_plan(graph, index, pipelines.TENANT)
    seed = tmp_path / "seed.json"
    seed.write_text('{"nodes": {}, "edges": []}')
    client = restricted_writer_client(
        SimpleNamespace(uses_record_store=False, neo4j_uri="", voice_session_signing_key=None),
        command=sys.executable,
        args=["-m", "backend.app.experiments.probabilistic_kg.fake_neo4j_mcp"],
        env_builder=lambda _s, env: {"PATH": env.get("PATH", ""), "FAKE_NEO4J_SEED": str(seed),
                                     "NEO4J_MCP_READ_ONLY": "true"},
    )

    async def run():
        try:
            tools = {tool["name"] for tool in await client.list_tools()}
            report = await McpGraphWriter(client).write(plan)
            return tools, report
        finally:
            await client.close()

    tools, report = asyncio.run(run())
    assert "write-cypher" not in tools
    assert report.error and not report.ok
    assert report.write_calls == 0 and report.applied_before_error == 0


def test_writer_environment_requires_isolated_credentials():
    settings = SimpleNamespace(neo4j_uri="neo4j+s://production.example")
    with pytest.raises(MCPConnectionError):
        build_writer_env(settings, {})
    with pytest.raises(MCPConnectionError):
        build_writer_env(settings, {"DESKMATE_EXP_NEO4J_URI": "neo4j+s://production.example",
                                    "DESKMATE_EXP_NEO4J_USERNAME": "u",
                                    "DESKMATE_EXP_NEO4J_PASSWORD": "p"})
    env = build_writer_env(settings, {"DESKMATE_EXP_NEO4J_URI": "bolt://localhost:7687",
                                      "DESKMATE_EXP_NEO4J_USERNAME": "u",
                                      "DESKMATE_EXP_NEO4J_PASSWORD": "p", "PATH": "/bin"})
    assert env["NEO4J_MCP_READ_ONLY"] == "false" and env["NEO4J_MCP_TELEMETRY"] == "false"


def test_restricted_writer_client_exposes_only_cypher_tools():
    client = restricted_writer_client(SimpleNamespace(uses_record_store=False, neo4j_uri=""))
    from backend.app.mcp.client import MCPToolError

    with pytest.raises(MCPToolError):
        asyncio.run(client.call_tool("search_knowledge", {}))


def test_agentic_retrieval_is_bounded_and_skips_cycles(index):
    view = GraphView("test")
    for name in ("Hub", *(f"Spoke {i}" for i in range(12))):
        view.entities[name] = {"name": name, "aliases": []}
    view.facts = [Fact(str(i), "Hub", "LINKS", f"Spoke {i}", "d", []) for i in range(12)]
    view.facts += [Fact("back", "Spoke 0", "LINKS", "Hub", "d", [])]  # a cycle
    answer = Retriever(view, index).agentic({"seeds": ["Hub"], "hops": [["LINKS"]] * 6})
    assert answer.graph_operations <= 4  # seed + at most MAX_HOPS expansions
    assert len(answer.values) <= 5      # fan-out cap


def test_exact_questions_never_reach_agentic_planning(index):
    answer = Retriever(GraphView("empty"), index).answer(
        "Is the position permanent?", {"seeds": ["x"], "hops": [["Y"]]}
    )
    assert (answer.strategy, answer.answer, answer.model_calls) == ("exact_form", "No", 0)
