"""Offline: Azure DI fixture -> form/table interpretation -> graph writes -> graph read -> answer.

A real LocalDocumentRepository processes an invented extraction (temporary
Chroma/SQLite, stub embeddings) and links it into an injected in-memory graph.
The agent's exact lookup then reads it through a neo4j-mcp read-cypher
harness. No Azure, Groq or Neo4j Aura request is made.
"""

import asyncio
from types import SimpleNamespace

import pytest
from backend.app.agent.graph import ServiceDeskAgent
from backend.app.agent.models import InvestigationDecision
from backend.app.core.principal import Principal, principal_scope
from backend.app.store.graph_repo import (
    EXACT_FORM_FIELDS_QUERY,
    EXACT_TABLE_CELLS_QUERY,
    GraphRepository,
    InMemoryGraphRepository,
)

pytestmark = pytest.mark.unit
TITLE = "Fictional Harbour Employment Form"
LONG_TERMS = ("Employees must give written notice before any change of schedule. " * 40).strip()


def _region(page, top):
    return [{"page": page, "polygon": [{"x": 1, "y": top}, {"x": 7, "y": top},
                                        {"x": 7, "y": top + 0.2}, {"x": 1, "y": top + 0.2}]}]


def _extraction():
    paragraphs, marks, offset = [], [], 0

    def para(page, text, role=None, mark_states=()):
        nonlocal offset
        position = sum(1 for p in paragraphs if p["page"] == page)
        start = offset
        offset += len(text) + 2
        paragraphs.append({"id": f"p-{page}-{position}", "page": page, "position": position,
                           "role": role, "text": text,
                           "spans": [{"offset": start, "length": len(text)}],
                           "bounding_regions": _region(page, 1 + position * 0.3)})
        tokens = [index for index in range(len(text)) if text.startswith(":selected:", index)
                  or text.startswith(":unselected:", index)]
        for token, state in zip(tokens, mark_states, strict=True):
            marks.append({"id": f"selectionmark-{page}-{len(marks)}", "page": page,
                          "state": state, "confidence": 0.991,
                          "span": {"offset": start + token, "length": 10},
                          "bounding_regions": _region(page, 1 + position * 0.3)})

    para(1, "Applicant Details", role="sectionHeading")
    para(1, "1. Is the applicant currently working for you? :selected: Yes :unselected: No",
         mark_states=("selected", "unselected"))
    para(1, "2. Is the position seasonal? :unselected: Yes :selected: No",
         mark_states=("unselected", "selected"))
    para(1, "3. Does the applicant hold a forklift licence? :selected: Yes :selected: No",
         mark_states=("selected", "selected"))
    para(1, "Terms and Conditions", role="sectionHeading")
    para(1, LONG_TERMS)
    para(2, "Employment Details", role="sectionHeading")
    cells = [
        (0, 0, "Record", "columnHeader"), (0, 1, "Job Title", "columnHeader"),
        (0, 2, "Annual Salary", "columnHeader"),
        (1, 0, "Employment Record", "rowHeader"), (1, 1, "Support Technician", "content"),
        (1, 2, "$50,000", "content"),
    ]
    table_cells = []
    for row, column, text, kind in cells:
        start = offset
        offset += len(text) + 1
        table_cells.append({"row": row, "column": column, "text": text, "kind": kind,
                            "row_span": 1, "column_span": 1,
                            "spans": [{"offset": start, "length": len(text)}],
                            "bounding_regions": _region(2, 2 + row * 0.3)})
    return {
        "pages": [{"page": 1, "text": "", "markdown": "", "spans": [], "selection_marks": marks},
                  {"page": 2, "text": "", "markdown": "", "spans": [], "selection_marks": []}],
        "paragraphs": paragraphs, "sections": [], "figures": [],
        "tables": [{"id": "table-2-0", "page": 2, "row_count": 2, "column_count": 3,
                    "cells": table_cells, "bounding_regions": _region(2, 2)}],
        "method": "document_intelligence", "metadata": {},
    }


class _Extractor:
    def extract_path(self, *args, **kwargs):
        return _extraction()


class _ReadCypherHarness:
    """neo4j-mcp read-cypher for the fixed exact queries, plus local resolution tools."""

    def __init__(self, graph, repository):
        self.graph, self.repository, self.calls = graph, repository, []

    def allowed_names(self, workflow):
        return {"neo4j__read-cypher", "search_knowledge"}

    async def invoke(self, name, arguments):
        self.calls.append(name)
        if name == "neo4j__read-cypher":
            query, ids = arguments["query"], arguments["params"]["document_ids"]
            rows = {EXACT_FORM_FIELDS_QUERY: self.graph.exact_form_fields,
                    EXACT_TABLE_CELLS_QUERY: self.graph.exact_table_cells}[query](ids)
            return {"items": rows, "status": "ok"}
        if name == "resolve_graph_references":
            return {"items": self.repository.search_store.resolve_object_ids(
                arguments["object_ids"]), "status": "ok"}
        if name == "resolve_table_cells":
            return {"items": self.repository.table_cells(arguments["document_id"],
                                                         arguments["table_ids"]), "status": "ok"}
        raise AssertionError(f"unexpected tool {name}")


def _ingest(settings, tmp_path, policy):
    from backend.app.services.local_documents import LocalDocumentRepository

    config = settings.model_copy(update={
        "local_persistence_enabled": True, "local_documents_dir": tmp_path / f"docs-{policy}",
        "chroma_dir": tmp_path / f"chroma-{policy}", "graph_content_policy": policy,
    })
    repository = LocalDocumentRepository(config)
    repository.extractor = _Extractor()
    repository.test_graph = graph = InMemoryGraphRepository()
    upload = repository.upload(b"%PDF-1.4 fictional form " + policy.encode(), TITLE,
                               "application/pdf", "structured_graph", "azure_di")
    repository.process(upload["id"])
    state = repository.enrich_graph(upload["id"])
    return config, repository, graph, upload["id"], state


def _answer(config, graph, repository, document_id, question):
    agent = ServiceDeskAgent.__new__(ServiceDeskAgent)
    agent.settings, agent._request_events = config, {}
    agent.harness = _ReadCypherHarness(graph, repository)
    state = {"ticket": {"summary": question}, "intent": {}, "executed_tool_calls": [],
             "resolved_document_ids": [document_id], "case_id": "c", "persist_case": False,
             "document_alias_matches": [{"id": document_id, "title": TITLE}]}
    exact = asyncio.run(agent._exact_structured_lookup(state))
    assert exact is not None, question
    evidence = agent._evidence(state, "search_knowledge", exact["output"])
    decision = InvestigationDecision(evidence_summary="", enough_information=False,
                                     confidence=0, outcome="cannot_verify")
    state["evidence"] = evidence
    agent._enforce_form_selection(state, decision)
    agent._enforce_table_cell(state, decision)
    return decision, agent.harness.calls, evidence[0]


@pytest.mark.parametrize("policy", ["structured_values", "references"])
def test_ingested_document_becomes_a_meaningful_graph_and_answers_exactly(
    settings, tmp_path, policy,
):
    with principal_scope(Principal("tenant-a", "owner")):
        config, repository, graph, document_id, state = _ingest(settings, tmp_path, policy)
        assert state["graph_link_status"] == "linked"
        assert state["graph_projection_version"] == 2
        assert state["graph_content_policy"] == policy
        values = policy == "structured_values"

        fields = {row["question"]: row for row in graph.exact_form_fields([document_id])}
        working = fields["1. Is the applicant currently working for you?"]
        assert working["status"] == "answered" and working["field_type"] == "single_choice"
        assert working["question_normalized"] == "is the applicant currently working for you"
        assert [o["selected"] for o in working["options"]] == [True, False]
        assert [o["ordinal"] for o in working["options"]] == [0, 1]
        assert working["selected_option"] == ("Yes" if values else None)
        assert [o["label"] for o in working["options"]] == (
            ["Yes", "No"] if values else [None, None]
        )
        licence = fields["3. Does the applicant hold a forklift licence?"]
        assert licence["status"] == "ambiguous" and licence["selected_option"] is None
        assert licence["ambiguity_reason"] == "multiple_selected"

        cells = graph.exact_table_cells([document_id])
        salary = next(c for c in cells if (c["row"], c["column"]) == (1, 2))
        assert (salary["data_type"], salary["unit"]) == ("currency", "$")
        # Table text, headers included, is a document value under the content policy.
        assert salary["value"] == ("$50,000" if values else None)
        assert salary["column_header"] == ("Annual Salary" if values else None)
        assert salary["row_header"] == ("Employment Record" if values else None)

        nodes = graph.references
        links = graph.reference_links
        sections = {n["properties"].get("title"): oid for oid, n in nodes.items()
                    if n["object_type"] == "Section"}
        table = next(oid for oid, n in nodes.items() if n["object_type"] == "Table")
        assert nodes[table]["properties"].get("column_headers") == (
            ["Record", "Job Title", "Annual Salary"] if values else None
        )
        assert nodes[table]["properties"]["row_count"] == 2
        assert (sections["Employment Details"], "HAS_TABLE", table) in links
        assert any(r == "HAS_FORMFIELD" and s == sections["Applicant Details"]
                   for s, r, _t in links)
        header = next(oid for oid, n in nodes.items() if n["object_type"] == "TableCell"
                      and (n["table_row"], n["table_column"]) == (0, 2))
        assert nodes[header]["properties"]["kind"] == "columnHeader"
        salary_cell = salary["object_id"]
        assert (header, "HEADER_OF", salary_cell) in links
        # The long Terms passage stays in Chroma; its paragraph reference links to it.
        terms = next(oid for oid, n in nodes.items() if n["object_type"] == "Paragraph"
                     and n["page"] == 1 and n["position"] == 5)
        chunk = next(t for s, r, t in links if s == terms and r == "PART_OF")
        assert "text" not in nodes[terms]["properties"]
        hydrated = repository.search_store.resolve_object_ids([chunk])
        assert LONG_TERMS[:200] in hydrated[0]["text"]
        assert nodes[sections["Terms and Conditions"]]["properties"]["content_references"] >= 1
        assert not any(n["description"].endswith("reference on page 2")
                       for n in nodes.values() if n["object_type"] == "Table")

        # Exact questions are answered by graph lookup, never by similarity search.
        for question, expected in [
            ("Is the applicant currently working for you?", "Yes"),
            ("Is the position seasonal?", "No"),
        ]:
            decision, calls, item = _answer(config, graph, repository, document_id, question)
            assert decision.outcome == "answer" and f"“{expected}”" in decision.answer
            assert "search_knowledge" not in calls
            assert calls == (["neo4j__read-cypher"] if values
                             else ["neo4j__read-cypher", "resolve_graph_references"])
            assert item["content"]["metadata"]["match_type"] == "exact_graph_lookup"
        decision, calls, _ = _answer(config, graph, repository, document_id,
                                     "Does the applicant hold a forklift licence?")
        assert decision.outcome == "cannot_verify" and "ambiguous" in decision.answer
        decision, calls, item = _answer(config, graph, repository, document_id,
                                        "What annual salary is listed for the employment record?")
        assert decision.outcome == "answer" and "$50,000" in decision.answer
        assert "search_knowledge" not in calls
        assert item["content"]["storage"] == ("neo4j" if values else "extraction")


def test_neo4j_writes_carry_meaning_and_respect_the_content_policy():
    """The Cypher rows sent to Neo4j hold labels and values, gated by policy."""
    from backend.app.services.local_documents import LocalDocumentRepository

    for policy in ("structured_values", "references"):
        written = []

        class Driver:
            def __init__(self, sink):
                self.sink = sink

            def execute_write(self, callback):
                return callback(SimpleNamespace(
                    run=lambda text, **values: self.sink.append((text, values))
                ))

        repo = GraphRepository(Driver(written), tenant_provider=lambda: "tenant-a")
        owner = SimpleNamespace(settings=SimpleNamespace(chunk_tokens=400,
                                                         graph_content_policy=policy))
        LocalDocumentRepository._publish_reference_objects(owner, repo, "doc", TITLE,
                                                           _extraction())
        rows = [row for query, params in written if "n += row.properties" in query
                for row in params["rows"]]
        props = [row["properties"] for row in rows]
        field = next(p for p in props if p.get("question", "").startswith("1."))
        marks = [p for p in props if "option_label" in p]
        cell = next(p for p in props if p.get("kind") == "content" and p.get("unit") == "$")
        if policy == "structured_values":
            assert field["selected_option"] == "Yes"
            assert {m["option_label"] for m in marks} >= {"Yes", "No"}
            assert cell["value"] == "$50,000"
        else:
            # Values are explicitly cleared, so a policy downgrade removes them from Aura.
            assert field["selected_option"] is None
            assert all(m["option_label"] is None for m in marks)
            assert cell["value"] is None and cell["row_header"] is None
            assert cell["column_header"] is None
            # Azure may mark filled-in values as header cells, so no table text leaves.
            assert all(p.get("header_text") is None and p.get("column_headers") is None
                       for p in props)
            descriptions = " ".join(row["description"] for row in rows)
            assert "Annual Salary" not in descriptions and "$50,000" not in descriptions
        assert field["question_normalized"] == "is the applicant currently working for you"
        document = next(params for query, params in written if "MERGE (d:Document" in query)
        assert document["projection"]["projection_version"] == 2


def test_projection_upgrade_is_idempotent_and_never_calls_azure(settings, tmp_path):
    with principal_scope(Principal("tenant-a", "owner")):
        _config, repository, graph, document_id, _state = _ingest(
            settings, tmp_path, "references"
        )
        repository.extractor = SimpleNamespace(extract_path=None)  # any extraction call fails
        assert repository.upgrade_graph_projection() == [{"id": document_id, "status": "current"}]
        before = dict(graph.references)
        repository.settings = repository.settings.model_copy(
            update={"graph_content_policy": "structured_values"})
        result = repository.upgrade_graph_projection(document_id)
        assert result[0]["status"] == "upgraded"
        assert set(graph.references) == set(before)  # same stable IDs, nothing deleted
        fields = {row["question"]: row for row in graph.exact_form_fields([document_id])}
        assert fields["2. Is the position seasonal?"]["selected_option"] == "No"


def test_semantic_claims_are_labelled_and_unsupported_ones_are_not_facts():
    from backend.app.models.document_graph_models import DocumentGraphProposal
    from backend.app.services.document_graph_understanding import DocumentGraphUnderstanding

    understanding = DocumentGraphUnderstanding(
        SimpleNamespace(semantic_graph_max_entities_per_document=50,
                        semantic_graph_max_relationships_per_document=50), None, None,
    )
    understanding.object_text = {
        "C1": "Applicant: Maria Chen. Employer: Harbour Logistics Inc.",
        "C2": "Position title: Support Technician",
    }
    understanding.form_fields = {}
    proposal = DocumentGraphProposal.model_validate({
        "document_id": "doc",
        "entities": [
            {"temporary_id": "mc", "name": "Maria Chen", "entity_type": "Person",
             "evidence_object_ids": ["C1"]},
            {"temporary_id": "hl", "name": "Harbour Logistics Inc.", "entity_type": "Org",
             "evidence_object_ids": ["C1"]},
            {"temporary_id": "st", "name": "Support Technician", "entity_type": "Role",
             "evidence_object_ids": ["C2"]},
            {"temporary_id": "mgr", "name": "Regional Manager", "entity_type": "Role",
             "evidence_object_ids": ["C2"]},
        ],
        "relationships": [
            {"source_temporary_id": "mc", "target_temporary_id": "hl", "predicate": "WORKS_FOR",
             "evidence_object_ids": ["C1"], "confidence": 0.9},
            {"source_temporary_id": "mc", "target_temporary_id": "st", "predicate": "HAS_ROLE",
             "evidence_object_ids": ["C1", "C2"], "confidence": 1.0, "status": "observed"},
            {"source_temporary_id": "hl", "target_temporary_id": "st", "predicate": "EMPLOYS",
             "evidence_object_ids": ["C2"], "confidence": 1.0, "status": "observed"},
            {"source_temporary_id": "mc", "target_temporary_id": "mgr",
             "predicate": "REPORTS_TO", "evidence_object_ids": ["C2"], "confidence": 0.5},
        ],
    })
    bounded = understanding._bounded(proposal, "doc", {"C1", "C2"})
    status = {r.predicate: r.status for r in bounded.relationships}
    assert status == {"WORKS_FOR": "observed", "HAS_ROLE": "derived", "EMPLOYS": "hypothesis"}
    assert "Regional Manager" not in {e.name for e in bounded.entities}
    assert understanding.rejected_proposals[0]["reason"] == "entity_not_named_in_cited_evidence"

    written = []

    class Driver:
        def execute_write(self, callback):
            return callback(SimpleNamespace(run=lambda query, **params: (
                written.append(params) or SimpleNamespace(single=lambda: {"written": 1})
            )))

    GraphRepository(Driver(), tenant_provider=lambda: "t").upsert_knowledge_relationships([
        {"source_id": "a", "target_id": "b", "predicate": "EMPLOYS", "document_id": "doc",
         "status": "hypothesis"},
        {"source_id": "a", "target_id": "b", "predicate": "WORKS_FOR", "document_id": "doc",
         "status": "observed"},
    ])
    claims = [p for p in written if "verified" in p]
    assert [(p["status"], p["verified"]) for p in claims] == [
        ("hypothesis", False), ("observed", True)]
