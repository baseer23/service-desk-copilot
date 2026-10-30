"""Score approaches A, B and C against the fixed answer key.

Run offline:  .venv/bin/python -m backend.app.experiments.probabilistic_kg.evaluate

Everything is computed from the graphs the approaches actually produced (read
back from the store for B and C) against TRUTH, which is written from the
documents. Nothing here asks a model to judge its own output.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from types import SimpleNamespace

from backend.app.experiments.probabilistic_kg import pipelines
from backend.app.experiments.probabilistic_kg.fixtures import PLANNER_DECISIONS, TRUTH
from backend.app.experiments.probabilistic_kg.recorded import container, resolve_ref
from backend.app.experiments.probabilistic_kg.retrieval import Retriever
from backend.app.experiments.probabilistic_kg.sink import FakeDriver, TemplateGraphStore
from backend.app.experiments.probabilistic_kg.validation import normalize
from backend.app.experiments.probabilistic_kg.writers import (
    DriverGraphWriter,
    McpGraphWriter,
    restricted_writer_client,
)


def _match(names, truth_name, truth_aliases=()):
    wanted = {normalize(truth_name), *(normalize(a) for a in truth_aliases)}
    return bool({normalize(n) for n in names if n} & wanted)


def _truth_entity(view, entity_id):
    names = view.names(entity_id)
    for name, _kind, aliases in TRUTH["entities"]:
        if _match(names, name, aliases):
            return name
    return None


def score_graph(view, index):
    truth_names = [name for name, _k, _a in TRUTH["entities"]]
    matched = {}
    for entity_id in view.entities:
        name = _truth_entity(view, entity_id)
        if name:
            matched.setdefault(name, []).append(entity_id)
    entity_correct = sum(1 for e in view.entities if _truth_entity(view, e))
    duplicates = sum(len(ids) - 1 for ids in matched.values())

    def fact_truth(fact):
        subject, obj = _truth_entity(view, fact.subject), _truth_entity(view, fact.object)
        for index_, (s, predicates, o, _refs) in enumerate(TRUTH["relationships"]):
            if subject == s and obj == o and fact.predicate in predicates:
                return index_
        return None

    correct_facts = [(fact, fact_truth(fact)) for fact in view.facts]
    found = {i for _f, i in correct_facts if i is not None}
    hypotheses_true = {fact_truth(f) for f in view.hypotheses} - {None}

    strict = lenient = attributed = 0
    cited_chars = []
    for fact, truth_index in correct_facts:
        for value in fact.evidence_ids:
            record = index.get(value)
            if record is not None:
                cited_chars.append(len(record.text))
        if truth_index is None:
            continue
        attributed += 1
        refs = TRUTH["relationships"][truth_index][3]
        expected = {resolve_ref(index, r if ":" in r and r[0] in "fct" else "p:" + r)
                    for r in refs} - {None}
        containers = {container(index, value) for value in expected} - {None}
        cited = set(fact.evidence_ids)
        strict += bool(cited & expected)
        lenient += bool(cited & (expected | containers))

    contradiction_found = 0
    for left, right in TRUTH["contradictions"]:
        sides = [{resolve_ref(index, "p:" + r) for r in side} for side in (left, right)]
        covered = [
            set(item.get("evidence_ids", [])) for item in view.contradictions
        ] + [
            {e for f in view.facts if f.contested for e in f.evidence_ids}
        ]
        contradiction_found += any(all(side & evidence for side in sides) for evidence in covered)

    facts = len(view.facts)
    triples = {(f.subject, f.predicate, f.object) for f in view.facts}
    return {
        "entities": len(view.entities),
        "entity_precision": entity_correct / max(len(view.entities), 1),
        "entity_recall": len(matched) / len(truth_names),
        "duplicate_entities": duplicates,
        "facts": facts,
        "relationship_precision": sum(i is not None for _f, i in correct_facts) / max(facts, 1),
        "relationship_recall": len(found) / len(TRUTH["relationships"]),
        "hallucinated_facts": sum(i is None for _f, i in correct_facts),
        "hallucinated_examples": [
            f"{view.entities.get(f.subject, {}).get('name')} {f.predicate} "
            f"{view.entities.get(f.object, {}).get('name')}"
            for f, i in correct_facts if i is None
        ],
        "hypotheses": len(view.hypotheses),
        "true_facts_held_as_hypotheses": len(hypotheses_true - found),
        "attribution_strict": strict / max(attributed, 1),
        "attribution_lenient": lenient / max(attributed, 1),
        "mean_cited_chars": round(sum(cited_chars) / max(len(cited_chars), 1)),
        "repeated_fact_triples": facts - len(triples),
        "contradictions_detected": contradiction_found,
        "contradictions_expected": len(TRUTH["contradictions"]),
    }


def score_retrieval(view, index):
    retriever = Retriever(view, index)
    exact = []
    for item in TRUTH["exact_questions"]:
        answer = retriever.answer(item["question"])
        expected_evidence = resolve_ref(index, item["evidence"])
        exact.append({
            "id": item["id"], "answer": answer.answer, "strategy": answer.strategy,
            "correct": answer.answer == item["answer"],
            "evidence_correct": expected_evidence in answer.evidence_ids,
        })
    strategies = {}
    for mode in ("graph_first", "agentic"):
        rows = []
        for item in TRUTH["multi_hop_questions"]:
            decision = PLANNER_DECISIONS.get(item["id"])
            answer = retriever.answer(item["question"], decision, mode=mode)
            if item["answer"] == "CONFLICT":
                correct = answer.answer == "CONFLICT" and set(answer.values) == item["values"]
            else:
                correct = normalize(answer.answer or "") == normalize(item["answer"])
            rows.append({
                "id": item["id"], "answer": answer.answer, "correct": correct,
                "graph_operations": answer.graph_operations, "hydrated": answer.hydrated,
                "model_calls": answer.model_calls,
            })
        strategies[mode] = rows
    return {"exact": exact, "multi_hop": strategies}


def structural_completeness(index):
    """How much of the Azure DI extraction is addressable from the graph."""
    totals, addressable = {}, {}

    def count(kind, wanted, present):
        totals[kind] = totals.get(kind, 0) + len(wanted)
        addressable[kind] = addressable.get(kind, 0) + len(set(wanted) & present)

    for document_id, extraction in index.extractions.items():
        nodes = list(index.structures[document_id].nodes.values())

        def on_graph(label, key, nodes=nodes):
            return {key(n) for n in nodes if n["label"] == label}

        count("paragraphs", [str(p["id"]) for p in extraction.get("paragraphs", [])],
              on_graph("Paragraph", lambda n: str(n.get("source_element_id"))))
        count("table_cells",
              [(str(t["id"]), c["row"], c["column"]) for t in extraction["tables"]
               for c in t["cells"]],
              on_graph("TableCell", lambda n: (str(n.get("source_element_id")),
                                               n.get("table_row"), n.get("table_column"))))
        count("tables", [t["page"] for t in extraction.get("tables", [])],
              on_graph("Table", lambda n: n.get("page")))
        count("figures", [f["page"] for f in extraction.get("figures", [])],
              on_graph("Figure", lambda n: n.get("page")))
        count("selection_marks",
              [str(m["id"]) for p in extraction["pages"] for m in p["selection_marks"]],
              on_graph("SelectionMark", lambda n: str(n.get("source_element_id"))))
        count("pages", [p["page"] for p in extraction.get("pages", [])],
              on_graph("Page", lambda n: n.get("page")))
    # What GraphRepository.upsert_reference_objects stores on each node (read
    # from its SET clause). Everything else stays recoverable from the saved
    # extraction artifact through source_element_id / row / column.
    on_graph = {
        "page_and_regions": "on node (page, bounding_regions_json, up to 8 regions)",
        "reading_order": "on node (ordinal)",
        "span_offset_length": "on node (first span only)",
        "selection_state_and_confidence": "on node (state_flag, confidence)",
        "table_row_column": "on node (table_row, table_column)",
        "paragraph_role": "on node (azure_role)",
        "merged_cell_spans": "artifact only (row_span/column_span not stored)",
        "cell_header_kind": "artifact only (columnHeader/rowHeader not stored)",
        "paragraph_and_cell_confidence": "artifact only",
        "extraction_version": "document version on node; DI model/API version artifact only",
    }
    return {
        "elements": {k: f"{addressable[k]}/{totals[k]}" for k in totals},
        "attributes_on_graph_nodes": on_graph,
    }


async def run_c(index, plan):
    """Approach C over a real MCP stdio connection to the neo4j-mcp stand-in."""
    store = TemplateGraphStore()
    store.load_structure(index, pipelines.TENANT)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump(store.structure_seed(), handle)
        seed = handle.name

    def env(_settings, parent):
        return {"PATH": parent.get("PATH", ""), "HOME": parent.get("HOME", ""),
                "FAKE_NEO4J_SEED": seed, "NEO4J_MCP_READ_ONLY": "false"}

    settings = SimpleNamespace(uses_record_store=False, neo4j_uri="",
                               voice_session_signing_key=None)
    client = restricted_writer_client(
        settings, command=sys.executable,
        args=["-m", "backend.app.experiments.probabilistic_kg.fake_neo4j_mcp"], env_builder=env,
    )
    try:
        tools = {tool["name"] for tool in await client.list_tools()}
        writer = McpGraphWriter(client)
        report = await writer.write(plan)
        nodes, edges = await writer.export(pipelines.TENANT)
    finally:
        await client.close()
        os.unlink(seed)
    return report, nodes, edges, tools


def _canonical(nodes, edges):
    strip = {"created_at"}
    return (
        sorted(json.dumps({**n, "props": {k: v for k, v in n["props"].items() if k not in strip}},
                          sort_keys=True) for n in nodes),
        sorted(json.dumps(e, sort_keys=True) for e in edges),
    )


async def evaluate():
    index = pipelines.build_index()
    view_a, info_a = await pipelines.run_baseline(index)
    graph, plan, _proposals, calls = await pipelines.reason_and_validate(index)

    store_b = TemplateGraphStore()
    store_b.load_structure(index, pipelines.TENANT)
    driver = FakeDriver(store_b)
    writer_b = DriverGraphWriter(driver)
    report_b = writer_b.write(plan)
    nodes_b, edges_b = writer_b.export(pipelines.TENANT)
    view_b = pipelines.view_from_export("B", nodes_b, edges_b)

    report_c, nodes_c, edges_c, tools_c = await run_c(index, plan)
    view_c = pipelines.view_from_export("C", nodes_c, edges_c)

    results = {"structural": structural_completeness(index), "approaches": {}}
    for view, ingestion_calls in ((view_a, info_a["model_calls"]), (view_b, len(calls)),
                                  (view_c, len(calls))):
        results["approaches"][view.approach] = {
            "graph": score_graph(view, index), "retrieval": score_retrieval(view, index),
            "ingestion_model_calls": ingestion_calls,
        }
    results["approaches"]["A"]["not_representable"] = info_a["not_representable"]
    results["validation"] = {
        "rejections": [{"type": r.item_type, "reason": r.reason,
                        "item": r.item.get("name") or r.item.get("predicate")
                        or r.item.get("field_evidence_id")} for r in graph.rejections],
        "demotions": graph.demotions,
        "form_readings_checked": graph.form_readings_checked,
        "form_readings_rejected": graph.form_readings_rejected,
    }
    results["transport"] = {
        "B": {key: getattr(report_b, key) for key in (
            "transport", "statements", "write_calls", "read_calls", "transactions", "atomic",
            "ok", "error")} | {"verified": report_b.verified},
        "C": {key: getattr(report_c, key) for key in (
            "transport", "statements", "write_calls", "read_calls", "transactions", "atomic",
            "ok", "error")} | {"verified": report_c.verified, "tools_offered": sorted(tools_c)},
        "identical_graphs": _canonical(nodes_b, edges_b) == _canonical(nodes_c, edges_c),
        "semantic_nodes": len(nodes_b), "semantic_relationships": len(edges_b),
    }
    return results


def main():
    results = asyncio.run(evaluate())
    json.dump(results, sys.stdout, indent=2, default=str)
    print()


if __name__ == "__main__":
    main()
