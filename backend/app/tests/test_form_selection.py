"""Offline form check-box accuracy and follow-up retrieval on invented forms.

Covers readable marks, question/option/mark association (span, geometry and
AcroForm), abstention on ambiguous or missing selections, graph references
without private values, grounded semantic understanding, and follow-ups that
resolve to the same form field.
"""

import asyncio
import io
from types import SimpleNamespace

import pytest
from backend.app.agent.graph import ServiceDeskAgent
from backend.app.agent.models import InvestigationDecision
from backend.app.services.chunking import split_structured
from backend.app.services.form_marks import acroform_fields, form_fields, readable_marks
from backend.app.store.graph_repo import InMemoryGraphRepository

pytestmark = pytest.mark.unit


def region(page, top, bottom, left=1.0, right=7.0):
    return [
        {
            "page": page,
            "polygon": [
                {"x": left, "y": top},
                {"x": right, "y": top},
                {"x": right, "y": bottom},
                {"x": left, "y": bottom},
            ],
        }
    ]


def paragraph(position, text, top, page=1, offset=None):
    return {
        "id": f"paragraph-{page}-{position}",
        "page": page,
        "position": position,
        "role": None,
        "text": text,
        "spans": [
            {"offset": offset if offset is not None else position * 100, "length": len(text)}
        ],
        "bounding_regions": region(page, top, top + 0.2),
    }


def mark(page, ordinal, state, confidence, offset, top):
    return {
        "id": f"selectionmark-{page}-{ordinal}",
        "page": page,
        "state": state,
        "confidence": confidence,
        "span": {"offset": offset, "length": 10},
        "bounding_regions": region(page, top, top + 0.15, 6.0, 6.2),
    }


# An invented mooring application laid out like a Document Intelligence result.
FORM = {
    "pages": [
        {
            "page": 1,
            "text": "",
            "markdown": "",
            "spans": [],
            "selection_marks": [
                # Paragraph 4's marks (offset 400): a smudged, low-confidence box.
                mark(1, 0, "selected", 0.41, 430, 4.0),
                mark(1, 1, "unselected", 0.95, 445, 4.0),
            ],
        }
    ],
    "paragraphs": [
        paragraph(0, "1. Is the vessel insured? :selected: Yes :unselected: No", 1.0),
        paragraph(1, "2. Is the berth covered? ☐ Yes ☒ No", 1.6),
        paragraph(2, "3. Is a trailer stored on site? :unselected: Yes :unselected: No", 2.2),
        paragraph(3, "4. Are pets aboard? :selected: Yes :selected: No", 2.8),
        paragraph(4, "5. Is the owner present? :selected: Yes :unselected: No", 4.0),
        # An unrelated box right under question 5.
        paragraph(5, ":unselected: Send me the harbour newsletter", 4.25),
        # Question 6 has no marks of its own; its boxes were read into the
        # instruction line below it.
        paragraph(6, "6. Has the hull been inspected?", 5.0),
        paragraph(
            7,
            "If yes, attach the report. If no, explain below: :unselected: Yes :selected: No",
            5.3,
        ),
    ],
    "sections": [],
    "tables": [],
    "figures": [],
    "method": "document_intelligence",
    "metadata": {},
}


def by_question(fields):
    return {field["question"]: field for field in fields}


def test_marks_read_the_same_whether_azure_wrote_tokens_or_boxes():
    assert readable_marks(":selected: Yes :unselected: No") == "[x] Yes [ ] No"
    assert readable_marks("☒ Yes ☐ No ☑ Maybe") == "[x] Yes [ ] No [x] Maybe"
    assert ServiceDeskAgent._readable_marks("☐ Yes ☒ No") == "[ ] Yes [x] No"


def test_each_question_keeps_its_own_marks_and_answers_only_when_certain():
    fields = by_question(form_fields(FORM))
    insured = fields["1. Is the vessel insured?"]
    assert (insured["status"], insured["selected_option"], insured["association_method"]) == (
        "answered",
        "Yes",
        "span",
    )
    assert fields["2. Is the berth covered?"]["selected_option"] == "No"  # ☒ / ☐ marks
    for question, reason in [
        ("3. Is a trailer stored on site?", "none_selected"),
        ("4. Are pets aboard?", "multiple_selected"),
        ("5. Is the owner present?", "low_confidence"),
    ]:
        assert fields[question]["status"] == "ambiguous", question
        assert fields[question]["selected_option"] is None
        assert fields[question]["ambiguity_reason"] == reason
    assert fields["5. Is the owner present?"]["confidence"] == pytest.approx(0.41)
    # The unrelated box is its own checklist item and leaves question 5 alone.
    assert fields["Send me the harbour newsletter"]["selected_option"] == "unchecked"
    # Marks on an instruction line belong to the nearest unanswered question.
    hull = fields["6. Has the hull been inspected?"]
    assert (hull["status"], hull["selected_option"], hull["association_method"]) == (
        "answered",
        "No",
        "geometry",
    )


def test_marks_equally_close_to_two_questions_are_not_assigned():
    tied = {
        **FORM,
        "pages": [{"page": 1, "text": "", "spans": []}],
        "paragraphs": [
            paragraph(0, "1. Is the gate locked?", 1.0),
            paragraph(
                1,
                "Please tick exactly one of the two boxes for the question: "
                ":selected: Yes :unselected: No",
                1.4,
            ),
            paragraph(2, "2. Is the light working?", 1.8),
        ],
    }
    (field,) = form_fields(tied)
    assert field["status"] == "ambiguous" and field["ambiguity_reason"] == "association_tie"
    assert field["selected_option"] is None


def test_form_fields_are_their_own_indexed_objects_with_question_and_options_together():
    objects = split_structured("d" * 64, FORM, 512)
    fields = [item for item in objects if item["object_type"] == "FormField"]
    insured = next(item for item in fields if item["question"] == "1. Is the vessel insured?")
    assert insured["text"] == "1. Is the vessel insured? — Yes [x] / No [ ] (selected: Yes)"
    assert insured["page"] == 1 and insured["options"] == ["Yes", "No"]
    owner = next(item for item in fields if item["question"] == "5. Is the owner present?")
    assert owner["text"].endswith("(selection not determinable: low confidence)")
    assert owner["mark_ids"] == ["selectionmark-1-0", "selectionmark-1-1"]
    # Chunks keep Azure's own tokens; readers see them through readable_marks.
    chunk = next(item for item in objects if item["object_type"] == "Chunk")
    assert ":selected: Yes" in chunk["text"]


def acroform_pdf():
    """A one-page fillable PDF: a checked box, a radio group and a text field."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        BooleanObject,
        DictionaryObject,
        FloatObject,
        NameObject,
        NumberObject,
        TextStringObject,
    )

    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)

    def appearance(*states):
        return DictionaryObject(
            {
                NameObject("/N"): DictionaryObject(
                    {NameObject(f"/{state}"): DictionaryObject() for state in states}
                )
            }
        )

    def rect(*values):
        return ArrayObject([FloatObject(value) for value in values])

    insured = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/FT"): NameObject("/Btn"),
            NameObject("/T"): TextStringObject("insured"),
            NameObject("/TU"): TextStringObject("Is the vessel insured?"),
            NameObject("/V"): NameObject("/Yes"),
            NameObject("/AS"): NameObject("/Yes"),
            NameObject("/AP"): appearance("Yes", "Off"),
            NameObject("/Rect"): rect(400, 700, 412, 712),
        }
    )
    berth = DictionaryObject(
        {
            NameObject("/FT"): NameObject("/Btn"),
            NameObject("/T"): TextStringObject("berth"),
            NameObject("/Ff"): NumberObject(1 << 15),
            NameObject("/V"): NameObject("/Open"),
        }
    )
    berth_ref = writer._add_object(berth)
    kids = []
    for index, state in enumerate(("Covered", "Open")):
        kid = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Annot"),
                NameObject("/Subtype"): NameObject("/Widget"),
                NameObject("/Parent"): berth_ref,
                NameObject("/AP"): appearance(state, "Off"),
                NameObject("/AS"): NameObject(f"/{state}" if state == "Open" else "/Off"),
                NameObject("/Rect"): rect(400 + 40 * index, 650, 412 + 40 * index, 662),
            }
        )
        kids.append(writer._add_object(kid))
    berth[NameObject("/Kids")] = ArrayObject(kids)
    vessel = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/FT"): NameObject("/Tx"),
            NameObject("/T"): TextStringObject("vessel"),
            NameObject("/V"): TextStringObject("Silver Tern"),
            NameObject("/Rect"): rect(100, 600, 300, 620),
        }
    )
    insured_ref, vessel_ref = writer._add_object(insured), writer._add_object(vessel)
    page[NameObject("/Annots")] = ArrayObject([insured_ref, *kids, vessel_ref])
    writer._root_object[NameObject("/AcroForm")] = DictionaryObject(
        {
            NameObject("/Fields"): ArrayObject([insured_ref, berth_ref, vessel_ref]),
            NameObject("/NeedAppearances"): BooleanObject(True),
        }
    )
    output = io.BytesIO()
    writer.write(output)
    output.seek(0)
    return output


def test_fillable_pdf_fields_are_read_locally_and_become_form_fields():
    fields = {item["name"]: item for item in acroform_fields(acroform_pdf())}
    assert fields["insured"]["kind"] == "checkbox" and fields["insured"]["value"] == "Yes"
    assert fields["berth"]["kind"] == "radio" and fields["berth"]["value"] == "Open"
    assert set(fields["berth"]["options"]) == {"Covered", "Open"}
    assert fields["vessel"] == {
        **fields["vessel"],
        "kind": "text",
        "value": "Silver Tern",
        "page": 1,
    }
    assert fields["insured"]["widget_rect"] == [400.0, 700.0, 412.0, 712.0]

    extraction = {"pages": [], "paragraphs": [], "tables": [], "form_fields": list(fields.values())}
    found = by_question(form_fields(extraction))
    assert found["Is the vessel insured?"]["selected_option"] == "Yes"
    assert found["berth"]["selected_option"] == "Open"
    assert {field["association_method"] for field in found.values()} == {"acroform"}


def test_graph_references_link_fields_and_marks_without_private_values():
    from backend.app.services.local_documents import LocalDocumentRepository

    graph = InMemoryGraphRepository()
    publisher = SimpleNamespace(settings=SimpleNamespace(chunk_tokens=512))
    acro = {**FORM, "form_fields": acroform_fields(acroform_pdf())}
    LocalDocumentRepository._publish_reference_objects(
        publisher, graph, "doc-1", "Mooring application.pdf", acro, version=1
    )
    fields = {
        key: ref for key, ref in graph.references.items() if ref["object_type"] == "FormField"
    }
    marks = {
        key: ref for key, ref in graph.references.items() if ref["object_type"] == "SelectionMark"
    }
    assert fields and marks
    insured = next(ref for ref in fields.values() if "Is the vessel insured?" in ref["description"])
    assert (insured["role"], insured["state_flag"]) == ("form_field", "answered")
    owner = next(ref for ref in fields.values() if "owner present" in ref["description"])
    assert owner["state_flag"] == "ambiguous" and owner["confidence"] == pytest.approx(0.41)
    links = {
        (source, target)
        for source, relation, target in graph.reference_links
        if relation == "HAS_SELECTIONMARK"
    }
    assert {target for _, target in links} == set(marks)
    assert {ref["state_flag"] for ref in marks.values()} == {"selected", "unselected"}
    assert {ref["role"] for ref in marks.values()} == {"option"}
    # References hold structure, never the answers: no selections or typed values.
    for ref in graph.references.values():
        assert "Silver Tern" not in ref["description"]
        assert "selected:" not in ref["description"] and "[x]" not in ref["description"]
    for ref in marks.values():
        # The default content policy keeps option labels (and so the answer) local.
        assert " — option " in ref["description"] and " of: " in ref["description"]
        assert "“" not in ref["description"] and "option_label" not in ref["properties"]


class ScriptedUnderstanding:
    """Proposes one grounded value and one value inferred from prose."""

    async def generate_structured(self, stage, context, schema):
        from backend.app.models.document_graph_models import DocumentGraphProposal

        if "objects" not in context:
            # Consolidation: hand back the validated section analysis unchanged.
            return DocumentGraphProposal.model_validate(context["section_analyses"][0])
        fields = {field["question"]: field for field in context["form_fields"]}
        insured = fields["1. Is the vessel insured?"]
        chunk = next(item for item in context["objects"] if item["object_type"] == "Chunk")
        return DocumentGraphProposal(
            document_id=context["document"]["id"],
            summary="A mooring application.",
            entities=[
                {
                    # Entity names must appear in the objects they cite.
                    "temporary_id": "vessel",
                    "name": "vessel",
                    "entity_type": "Vessel",
                    "evidence_object_ids": [chunk["object_id"]],
                },
                {
                    "temporary_id": "yes",
                    "name": "Yes",
                    "entity_type": "Answer",
                    "evidence_object_ids": [insured["object_id"]],
                },
                {
                    "temporary_id": "no",
                    "name": "No",
                    "entity_type": "Answer",
                    "evidence_object_ids": [chunk["object_id"]],
                },
            ],
            relationships=[
                # Grounded: the insured field's selected option, citing the field.
                {
                    "source_temporary_id": "vessel",
                    "target_temporary_id": "yes",
                    "predicate": "INSURED",
                    "evidence_object_ids": [insured["object_id"]],
                },
                # Invented from prose: no FormField says pets are "No".
                {
                    "source_temporary_id": "vessel",
                    "target_temporary_id": "no",
                    "predicate": "HAS_PETS_ABOARD",
                    "evidence_object_ids": [chunk["object_id"]],
                },
            ],
        )


def test_understanding_keeps_grounded_field_values_and_rejects_invented_ones(settings, tmp_path):
    from backend.app.services.document_graph_understanding import DocumentGraphUnderstanding
    from backend.app.services.local_documents import LocalDocumentRepository

    configured = settings.model_copy(
        update={
            "local_persistence_enabled": True,
            "data_platform": "local",
            "local_documents_dir": tmp_path / "documents",
            "semantic_graph_batch_chars": 1_000_000,
        }
    )
    records = LocalDocumentRepository(configured).records
    understanding = DocumentGraphUnderstanding(configured, ScriptedUnderstanding(), records)
    proposal, partial = asyncio.run(
        understanding.understand_document("doc-1", FORM, {"title": "Mooring application.pdf"})
    )
    assert not partial
    assert [item.predicate for item in proposal.relationships] == ["INSURED"]
    job = records.read("document_semantic_graph_job", understanding.job_key("doc-1", {}))
    assert job["payload"]["rejected_form_values"] == 1


# --- Answering and follow-ups -------------------------------------------------------


def field_evidence(identifier, question, status, selected="", reason="", page=1):
    return {
        "id": identifier,
        "source_type": "document",
        "tool": "search_knowledge",
        "source_system": "chroma",
        "timestamp": "2026-10-29T00:00:00Z",
        "reliability": 0.9,
        "content": {
            "id": identifier,
            "title": "Mooring application.pdf",
            "text": question,
            "metadata": {
                "doc_id": "doc-1",
                "page": page,
                "object_type": "FormField",
                "form_field_question": question,
                "form_field_status": status,
                "form_field_selected": selected,
                "form_ambiguity_reason": reason,
            },
        },
    }


def decision(answer="It means the vessel has cover.", outcome="answer"):
    return InvestigationDecision(
        outcome=outcome,
        evidence_summary="",
        enough_information=True,
        answer=answer,
        confidence=0.6,
    )


def agent_state(question, standalone, evidence):
    return {
        "workflow": "knowledge",
        "ticket": {"summary": question},
        "intent": {"standalone_question": standalone},
        "evidence": evidence,
    }


def test_a_selection_is_answered_only_from_an_answered_field_with_its_page():
    state = agent_state(
        "Is the vessel insured marked yes or no?",
        "In Mooring application.pdf, is 'Is the vessel insured?' marked yes or no?",
        [field_evidence("f-insured", "1. Is the vessel insured?", "answered", "Yes", page=3)],
    )
    result = decision()  # explains meaning instead of answering
    ServiceDeskAgent._enforce_form_selection(ServiceDeskAgent, state, result)
    assert result.outcome == "answer"
    assert result.answer == "The form marks “Yes” for “1. Is the vessel insured?” (page 3)."
    assert result.hypotheses[0].evidence_ids == ["f-insured"]


@pytest.mark.parametrize(
    "evidence, expected",
    [
        (
            [
                field_evidence(
                    "f-pets", "4. Are pets aboard?", "ambiguous", reason="multiple_selected"
                )
            ],
            "its check boxes are ambiguous (multiple selected)",
        ),
        ([], "no check-box selection was extracted for it from the document"),
    ],
)
def test_ambiguous_or_missing_selections_are_not_guessed(evidence, expected):
    state = agent_state(
        "Are pets aboard, is it a yes or no?",
        "In Mooring application.pdf, is 'Are pets aboard?' marked yes or no?",
        evidence,
    )
    result = decision(answer="Yes, pets are aboard.")
    ServiceDeskAgent._enforce_form_selection(ServiceDeskAgent, state, result)
    assert result.outcome == "cannot_verify" and result.hypotheses == []
    assert expected in result.answer and "Yes, pets" not in result.answer


def test_follow_up_is_it_a_no_resolves_to_the_same_field(make_client):
    from backend.app.tests.test_graph_first_documents import FakeNeo4j, _connect_neo4j

    standalone = "In Mooring application.pdf, is the answer to 'Is the vessel insured?' No?"
    state = {
        "case_id": "follow-up",
        "persist_case": False,
        "workflow": "knowledge",
        "ticket": {"summary": "is it a no?"},
        "intent": {"standalone_question": standalone},
        "conversation_context": {
            "previous_request": "Is the vessel insured marked yes or no?",
            "document_references": [{"document_id": "doc-1", "page": 1}],
        },
        "resolved_document_ids": ["doc-1"],
        "evidence": [field_evidence("f-insured", "1. Is the vessel insured?", "answered", "Yes")],
    }
    assert ServiceDeskAgent._retrieval_question(state) == standalone
    assert ServiceDeskAgent._retrieval_question({"ticket": {"summary": "raw"}}) == "raw"

    # The graph read searches for the field, not for the words "is it a no".
    client = make_client()
    agent = client.app.state.agent
    fake = FakeNeo4j(rows=[])
    _connect_neo4j(client, fake)
    client.portal.call(agent._mandatory_saved_knowledge_graph_read, state)
    terms = " ".join(fake.calls[0]["params"]["terms"]).casefold()
    assert "vessel" in terms or "insured" in terms

    # The selected field answers the follow-up: it is not a no.
    result = decision(answer="No.")
    ServiceDeskAgent._enforce_form_selection(ServiceDeskAgent, state, result)
    assert result.answer == "The form marks “Yes” for “1. Is the vessel insured?” (page 1)."


def test_graph_selection_adds_the_matching_field_without_using_a_slot():
    rows = [
        {
            "id": f"c{n}",
            "text": f"Harbour rules paragraph {n}.",
            "metadata": {
                "doc_id": "doc-1",
                "page": 2,
                "object_type": "Chunk",
                "object_id": f"c{n}",
            },
        }
        for n in range(10)
    ]
    rows += [
        {
            "id": "f1",
            "text": "1. Is the vessel insured? — Yes [x] / No [ ] (selected: Yes)",
            "metadata": {
                "doc_id": "doc-1",
                "page": 1,
                "object_type": "FormField",
                "object_id": "f1",
                "form_field_question": "1. Is the vessel insured?",
            },
        },
        {
            "id": "f2",
            "text": "4. Are pets aboard? — Yes [x] / No [x]",
            "metadata": {
                "doc_id": "doc-1",
                "page": 1,
                "object_type": "FormField",
                "object_id": "f2",
                "form_field_question": "4. Are pets aboard?",
            },
        },
    ]
    selected = ServiceDeskAgent._select_graph_evidence(rows, "Is the vessel insured?", 4)
    ids = [row["id"] for row in selected]
    assert ids[0] == "f1" and "f2" not in ids
    assert len([row for row in selected if row["metadata"]["object_type"] == "Chunk"]) >= 4


class FakeLayoutClient:
    """A Document Intelligence layout result with selection marks, as the SDK shapes it."""

    def begin_analyze_document(self, model, **arguments):
        content = "Is the vessel insured?\n☒ Yes ☐ No"
        span = lambda offset, length: SimpleNamespace(offset=offset, length=length)  # noqa: E731
        page = SimpleNamespace(
            page_number=1,
            spans=[span(0, len(content))],
            lines=[
                SimpleNamespace(content="Is the vessel insured?"),
                SimpleNamespace(content=":selected: Yes :unselected: No"),
            ],
            selection_marks=[
                SimpleNamespace(
                    state="selected",
                    confidence=0.98,
                    span=span(23, 1),
                    polygon=[6.0, 1.0, 6.2, 1.0, 6.2, 1.2, 6.0, 1.2],
                ),
                SimpleNamespace(
                    state="unselected",
                    confidence=0.97,
                    span=span(29, 1),
                    polygon=[6.6, 1.0, 6.8, 1.0, 6.8, 1.2, 6.6, 1.2],
                ),
            ],
        )
        result = SimpleNamespace(
            content=content, pages=[page], paragraphs=[], tables=[], figures=[]
        )
        return SimpleNamespace(result=lambda timeout=None: result, details={})


def test_extractor_saves_selection_marks_and_azure_markdown(settings):
    from backend.app.services.document_processing import AzureDocumentExtractor, _offset_batch

    extractor = AzureDocumentExtractor(settings, records=None, client=FakeLayoutClient())
    result = extractor.extract(b"image", "image/png")
    page = result["pages"][0]
    assert page["text"] == "Is the vessel insured?\n:selected: Yes :unselected: No"
    assert page["markdown"] == "Is the vessel insured?\n[x] Yes [ ] No"  # Azure's ☒ / ☐
    first = page["selection_marks"][0]
    assert {key: first[key] for key in ("id", "page", "state", "confidence", "span")} == {
        "id": "selectionmark-1-0",
        "page": 1,
        "state": "selected",
        "confidence": 0.98,
        "span": {"offset": 23, "length": 1},
    }
    assert first["bounding_regions"][0]["polygon"][0] == {"x": 6.0, "y": 1.0}
    # A later batch's marks are renumbered by document page, so their IDs stay stable.
    shifted = _offset_batch(result, 4, 2)
    assert [mark["id"] for mark in shifted["pages"][0]["selection_marks"]] == [
        "selectionmark-5-0",
        "selectionmark-5-1",
    ]
    assert shifted["pages"][0]["selection_marks"][0]["bounding_regions"][0]["page"] == 5


def test_grader_keeps_the_field_asked_about_even_if_the_model_rejects_it(make_client):
    from backend.app.agent.models import EvidenceGradeResult

    client = make_client()
    agent = client.app.state.agent

    async def reject_everything(stage, context, schema):
        return EvidenceGradeResult(
            accepted_ids=[],
            rejected=[
                {"id": item["id"], "reason": "Looks like boilerplate."}
                for item in context["candidates"]
            ],
        )

    agent.provider.generate_structured = reject_everything
    state = agent_state("is it a no?", "Is 'Is the vessel insured?' marked No?", [])
    candidates = [
        field_evidence("f-insured", "1. Is the vessel insured?", "answered", "Yes"),
        field_evidence("f-pets", "4. Are pets aboard?", "ambiguous", reason="multiple_selected"),
    ]
    graded = client.portal.call(agent._grade_document_evidence, state, candidates)
    assert [item["id"] for item in graded["accepted"]] == ["f-insured"]
    assert [item["evidence"]["id"] for item in graded["rejected"]] == ["f-pets"]
