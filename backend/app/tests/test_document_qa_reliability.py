"""Offline regressions for document question answering reliability.

Every document here is invented. The checks cover page-accurate chunks,
readable form check boxes, fused graph evidence selection, de-duplication,
document scope for follow-ups and partial names, documented graph
relationships, and the reusable evaluation harness.
"""

import json

import pytest
from backend.app.agent.graph import ServiceDeskAgent
from backend.app.evaluation.document_eval import evaluate, temporary_settings
from backend.app.services.chunking import split_structured

pytestmark = pytest.mark.unit


def paragraph(page, position, text, role=None):
    return {
        "id": f"p-{page}-{position}",
        "page": page,
        "position": position,
        "text": text,
        "role": role,
        "spans": [],
    }


# A made-up three-page permit form laid out like a Document Intelligence result.
FORM = {
    "pages": [{"page": n, "text": "", "markdown": "", "spans": []} for n in (1, 2, 3)],
    "paragraphs": [
        paragraph(1, 0, "Section A - Applicant", "sectionHeading"),
        paragraph(1, 1, "Applicant name: Rowan Ellery"),
        paragraph(1, 2, "Vessel name: Silver Tern"),
        paragraph(2, 3, "Mooring berth: East Basin 14"),
        paragraph(2, 4, "1. Is the vessel insured? :unselected: Yes :selected: No"),
        paragraph(2, 5, "Harbour Permit 77 - Mooring Form Page 2 of 3", "pageFooter"),
        paragraph(3, 6, "Section B - Fees", "sectionHeading"),
        paragraph(3, 7, "Annual mooring fee: $1,240"),
        paragraph(3, 8, "Harbour Permit 77 - Mooring Form Page 3 of 3", "pageFooter"),
    ],
    "sections": [],
    "tables": [
        {
            "id": "table-3-0",
            "page": 3,
            "row_count": 2,
            "column_count": 2,
            "cells": [
                {"row": 0, "column": 0, "text": "Item"},
                {"row": 0, "column": 1, "text": "Due"},
                {"row": 1, "column": 0, "text": "Fee"},
                {"row": 1, "column": 1, "text": "1 March"},
            ],
        }
    ],
    "figures": [],
    "method": "document_intelligence",
    "metadata": {},
}


def test_chunks_never_span_pages_so_their_page_is_a_correct_citation():
    chunks = [
        item for item in split_structured("d" * 64, FORM, 512) if item["object_type"] == "Chunk"
    ]
    for chunk in chunks:
        pages = {p["page"] for p in FORM["paragraphs"] if p["text"] in chunk["text"]}
        assert pages == {chunk["page"]}, chunk["text"]
    berth = next(chunk for chunk in chunks if "East Basin 14" in chunk["text"])
    assert berth["page"] == 2 and "Silver Tern" not in berth["text"]


def test_saved_extraction_without_sections_regains_them_from_headings():
    from backend.app.services.document_processing import with_heading_sections

    assert FORM["sections"] == []
    restored = with_heading_sections(FORM)
    assert [s["title"] for s in restored["sections"]] == [
        "Section A - Applicant",
        "Section B - Fees",
    ]
    objects = split_structured("d" * 64, restored, 512)
    sections = {o["source_id"]: o for o in objects if o["object_type"] == "Section"}
    fee = next(o for o in objects if o["object_type"] == "Chunk" and "$1,240" in o["text"])
    assert sections[fee["section_id"]]["text"] == "Section B - Fees"
    assert FORM["sections"] == []  # the saved artifact itself is never modified


def test_form_check_boxes_are_readable_to_the_model():
    text = "1. Is the vessel insured? :unselected: Yes :selected: No"
    assert ServiceDeskAgent._readable_marks(text) == "1. Is the vessel insured? [ ] Yes [x] No"


def row(identifier, text, page, object_id=None):
    return {
        "id": identifier,
        "text": text,
        "metadata": {"doc_id": "doc", "page": page, "object_id": object_id or identifier},
    }


def test_graph_evidence_fuses_keywords_tolerates_typos_and_adds_page_neighbours():
    # Rows arrive in vector-similarity order; the fee passage ranks last by vector.
    rows = [row(f"filler-{n}", f"General harbour guidance paragraph {n}.", 9) for n in range(12)]
    rows += [
        row("fee", "Annual mooring fee: $1,240", 3),
        row("fee-table", "Item | Due\nFee | 1 March", 3),
    ]
    selected = ServiceDeskAgent._select_graph_evidence(rows, "whats the anual moring fee?", 4)
    ids = [item["id"] for item in selected]
    assert ids[0] == "fee"  # typo-tolerant keyword relevance outranks vector order
    assert "fee-table" in ids  # same-page neighbour from the graph's page structure
    assert len(selected) <= 4 + 4


def test_heading_only_section_rows_never_take_evidence_slots():
    heading = row("sec", "Section B - Fees", 3)
    heading["metadata"]["object_type"] = "Section"
    rows = [heading, row("fee", "Section B - Fees\n\nAnnual mooring fee: $1,240", 3)]
    selected = ServiceDeskAgent._select_graph_evidence(rows, "What are the fees?", 1)
    assert [item["id"] for item in selected] == ["fee"]


def test_passages_supporting_graph_claims_are_always_kept():
    rows = [row(f"r{n}", f"Unrelated text {n}", 1, f"obj-{n}") for n in range(20)]
    selected = ServiceDeskAgent._select_graph_evidence(rows, "anything", 3, priority_ids={"obj-19"})
    assert selected[0]["id"] == "r19"


def test_the_same_passage_from_two_paths_is_kept_once():
    graph = [row("c1", "A", 1), row("c2", "B", 2)]
    local = [row("c2", "B", 2), row("c3", "C", 3)]
    assert [item["id"] for item in ServiceDeskAgent._unique_document_rows([*graph, *local])] == [
        "c1",
        "c2",
        "c3",
    ]


INVENTORY = [
    {"id": "permit-77", "title": "Harbour Permit 77.pdf"},
    {"id": "hp-guide", "title": "HPA Mooring Guide.pdf"},
    {"id": "hp-rates", "title": "HPA Rates 2026.pdf"},
]


def test_document_scope_from_title_follow_up_and_acronym():
    scope = ServiceDeskAgent._scope_documents
    assert scope("What fee does harbour permit 77 set?", INVENTORY, documents_intent=True)[1] == [
        "permit-77"
    ]
    follow_up = {"document_references": [{"document_id": "permit-77", "page": 3}]}
    matches, resolved, kind = scope(
        "And when is it due?", INVENTORY, follow_up, documents_intent=True
    )
    assert resolved == ["permit-77"] and kind == "previous answer"
    # A title acronym (three or more capitals) naming two documents is asked
    # about, not guessed; two-letter acronyms are too often ordinary words.
    matches, resolved, kind = scope(
        "What does the HPA document say?", INVENTORY, documents_intent=True
    )
    assert {m["id"] for m in matches} == {"hp-guide", "hp-rates"} and resolved == []
    # Without a saved-document intent, weak signals never force document scope.
    assert scope("And when is it due?", INVENTORY, follow_up, documents_intent=False) == (
        [],
        [],
        "title",
    )


def test_graph_summary_carries_documented_relationships(make_client):
    from backend.app.tests.test_graph_first_documents import FakeNeo4j, _connect_neo4j

    client = make_client()
    agent = client.app.state.agent
    fake = FakeNeo4j(
        rows=[
            {
                "document_id": "permit-77",
                "title": "Harbour Permit 77.pdf",
                "summary": None,
                "entities": ["Rowan Ellery", "Silver Tern"],
                "mention_object_ids": [],
                "claim_object_ids": ["obj-owner"],
                "claims": [
                    {
                        "subject": "Rowan Ellery",
                        "predicate": "OWNS",
                        "object": "Silver Tern",
                        "evidence_object_ids": ["obj-owner"],
                    }
                ],
                "page_objects": [{"object_id": "obj-owner", "page": 1}],
            }
        ]
    )
    _connect_neo4j(client, fake)
    result = client.portal.call(
        agent._mandatory_saved_knowledge_graph_read,
        {
            "case_id": "claims",
            "persist_case": False,
            "ticket": {"summary": "Who owns the vessel in harbour permit 77?"},
            "resolved_document_ids": ["permit-77"],
        },
    )
    assert "AS claims" in fake.calls[0]["query"]
    summary = result["output"]["items"][0]
    assert summary["relationships"] == [
        {
            "subject": "Rowan Ellery",
            "predicate": "OWNS",
            "object": "Silver Tern",
            "supporting_object_ids": ["obj-owner"],
        }
    ]


def test_evaluation_harness_scores_identification_pages_and_duplicates(tmp_path):
    spec = {
        "document_id": "permit-77",
        "title": "Harbour Permit 77.pdf",
        "inventory_decoys": [{"id": "hp-guide", "title": "HPA Mooring Guide.pdf"}],
        "questions": [
            {
                "id": "A",
                "question": "What is the berth in harbour permit 77?",
                "expected": ["East Basin 14"],
                "pages": [2],
            },
            {
                "id": "B",
                "follow_up_of": "A",
                "question": "And the annual fee?",
                "expected": ["$1,240"],
                "pages": [3],
            },
            {
                "id": "C",
                "question": "Is the vessel insured per harbour permit 77?",
                "expected": [":unselected: Yes :selected: No"],
                "pages": [2],
            },
            {
                "id": "D",
                "question": "What is the owner's phone number in harbour permit 77?",
                "expected": [],
                "answerable": False,
            },
        ],
    }
    settings = temporary_settings(tmp_path, embed_provider="stub")
    results = {item.id: item for item in evaluate(json.loads(json.dumps(spec)), FORM, settings)}
    assert all(item.identification_ok for item in results.values())
    assert results["B"].notes == ["document identified by previous answer"]
    for key in ("A", "B", "C"):
        assert results[key].found and results[key].citation_ok, results[key]
    assert all(item.mislabelled == 0 and item.duplicates == 0 for item in results.values())
    assert all(item.retrieval_pass for item in results.values())


def test_observe_keeps_one_evidence_item_per_passage(make_client):
    from backend.app.agent.models import EvidenceGradeResult

    client = make_client()
    agent = client.app.state.agent

    async def grade(stage, context, schema):
        return EvidenceGradeResult(
            accepted_ids=[item["id"] for item in context["candidates"]], rejected=[]
        )

    agent.provider.generate_structured = grade
    passage = {
        "id": "chunk-fee",
        "text": "Annual mooring fee: $1,240",
        "score": 0.8,
        "metadata": {"doc_id": "permit-77", "title": "Harbour Permit 77.pdf", "page": 3},
    }
    output = {"source_system": "chroma", "timestamp": "2026-10-28T00:00:00Z", "reliability": 0.9}
    state = {
        "case_id": "dedupe",
        "persist_case": False,
        "workflow": "knowledge",
        "ticket": {"summary": "What is the annual fee in harbour permit 77?"},
        "evidence": [],
        "tool_results": [
            {
                "name": "search_knowledge",
                "output": {**output, "items": [{**passage, "match_type": "graph_reference"}]},
            },
            {
                "name": "search_knowledge",
                "output": {**output, "items": [{**passage, "match_type": "keyword"}]},
            },
        ],
    }
    update = client.portal.call(agent.observe, state)
    documents = [item for item in update["evidence"] if item["source_type"] == "document"]
    assert len(documents) == 1
    assert documents[0]["content"]["match_type"] == "graph_reference"
