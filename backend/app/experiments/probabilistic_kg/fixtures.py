"""Fixed evaluation dataset: invented documents, an answer key, and recorded model output.

Documents are shaped like DeskMate's saved Azure Document Intelligence
extraction (paragraphs with roles, spans, regions and confidence; tables with
header kinds and merged cells; selection marks; figures). Everything is
invented. Six documents:

    form     Employment verification application with Yes/No check boxes
    policy   Multi-section Terms and Conditions
    premiums Shift premium schedule: a table with merged header and body cells
    vendor   Vendor agreement            } related entities, and a conflicting
    minutes  Board minutes               } effective date for the same agreement
    manual   Ten-page operations manual with the key fact on page 9

TRUTH is written from the documents alone and is never derived from model
output. MODEL_READINGS is one recorded structured reading per document,
standing in for an LLM response. It deliberately contains realistic mistakes
(a misread check box, an entity not in the text, an overstated "observed"
claim, a wrong citation, a speculative link) so validation is exercised.
Approach A and Approach B receive the same reading, converted to each schema.
"""

from __future__ import annotations

# --- extraction builders ---------------------------------------------------------


class _Doc:
    def __init__(self):
        self.offset = 0
        self.paragraphs, self.tables, self.figures, self.marks = [], [], [], {}

    def para(self, pid, page, text, role=None, top=1.0, marks=()):
        position = sum(1 for p in self.paragraphs if p["page"] == page)
        offset = self.offset
        self.offset += len(text) + 2
        self.paragraphs.append({
            "id": pid, "page": page, "position": position, "role": role, "text": text,
            "spans": [{"offset": offset, "length": len(text)}],
            "bounding_regions": _region(page, top, top + 0.2),
            "confidence": 0.99,
        })
        for ordinal, (state, relative) in enumerate(marks):
            page_marks = self.marks.setdefault(page, [])
            page_marks.append({
                "id": f"selectionmark-{page}-{len(page_marks)}", "page": page, "state": state,
                "confidence": 0.97, "span": {"offset": offset + relative, "length": 10},
                "bounding_regions": _region(page, top, top + 0.15, 6.0 + ordinal, 6.2 + ordinal),
            })

    def table(self, tid, page, cells):
        built = []
        for row, column, text, kind, row_span, column_span in cells:
            offset = self.offset
            self.offset += len(text) + 1
            built.append({
                "row": row, "column": column, "text": text, "kind": kind,
                "row_span": row_span, "column_span": column_span, "confidence": 0.98,
                "spans": [{"offset": offset, "length": len(text)}],
                "bounding_regions": _region(page, 2.0 + row * 0.3, 2.3 + row * 0.3,
                                            1.0 + column * 2, 3.0 + column * 2),
            })
        self.tables.append({
            "id": tid, "page": page, "row_count": 1 + max(c["row"] for c in built),
            "column_count": 1 + max(c["column"] for c in built), "cells": built,
            "bounding_regions": _region(page, 2.0, 4.0),
        })

    def figure(self, fid, page, caption):
        self.figures.append({"id": fid, "page": page, "caption": caption,
                             "bounding_regions": _region(page, 5.0, 7.0)})

    def build(self):
        pages = sorted({p["page"] for p in self.paragraphs} | {t["page"] for t in self.tables})
        return {
            "pages": [{"page": page, "text": "", "markdown": "", "spans": [],
                       "selection_marks": self.marks.get(page, [])} for page in pages],
            "paragraphs": self.paragraphs, "sections": [], "tables": self.tables,
            "figures": self.figures, "method": "document_intelligence",
            "metadata": {"extraction_version": "fixture-1", "model": "prebuilt-layout"},
        }


def _region(page, top, bottom, left=1.0, right=7.0):
    return [{"page": page, "polygon": [{"x": left, "y": top}, {"x": right, "y": top},
                                        {"x": right, "y": bottom}, {"x": left, "y": bottom}]}]


def _form():
    d = _Doc()
    d.para("form-0", 1, "Employment Verification Application", role="title", top=0.5)
    d.para("form-1", 1, "Applicant: Maria Chen", top=1.0)
    d.para("form-2", 1, "Employer: Harbour Logistics Inc.", top=1.3)
    d.para("form-3", 1, "Business Number: 845123987", top=1.6)
    q1 = "1. Is the applicant currently employed by this employer? :selected: Yes :unselected: No"
    d.para("form-4", 1, q1, top=2.0, marks=[("selected", q1.index(":selected:")),
                                             ("unselected", q1.index(":unselected:"))])
    q2 = "2. Is the position permanent? :unselected: Yes :selected: No"
    d.para("form-5", 1, q2, top=2.4, marks=[("unselected", q2.index(":unselected:")),
                                             ("selected", q2.index(":selected:"))])
    q3 = "3. Does the applicant work more than 30 hours per week? :selected: Yes :unselected: No"
    d.para("form-6", 1, q3, top=2.8, marks=[("selected", q3.index(":selected:")),
                                             ("unselected", q3.index(":unselected:"))])
    d.para("form-7", 2, "Employment Details", role="sectionHeading", top=0.5)
    d.para("form-8", 2, "Position title: Warehouse Supervisor", top=1.0)
    d.para("form-9", 2, "Work location: Dartmouth warehouse", top=1.3)
    d.para("form-10", 2, "Start date: 2023-04-03", top=1.6)
    return d.build()


def _policy():
    d = _Doc()
    d.para("pol-0", 1, "Harbour Logistics Inc. Employment Terms and Conditions", role="title")
    d.para("pol-1", 1, "1. Hours of Work", role="sectionHeading", top=1.0)
    d.para("pol-2", 1, "Full-time employees work 40 hours per week. Employees working more than "
                       "30 hours per week are eligible for group benefits.", top=1.3)
    d.para("pol-3", 1, "2. Probation", role="sectionHeading", top=2.0)
    d.para("pol-4", 1, "New employees complete a probationary period of 90 days.", top=2.3)
    d.para("pol-5", 2, "3. Termination", role="sectionHeading", top=0.5)
    d.para("pol-6", 2, "After the probationary period, Harbour Logistics Inc. must provide two "
                       "weeks' written notice of termination.", top=1.0)
    d.para("pol-7", 2, "4. Benefits Provider", role="sectionHeading", top=2.0)
    d.para("pol-8", 2, "Group benefits are administered by Maritime Mutual Insurance.", top=2.3)
    return d.build()


def _premiums():
    d = _Doc()
    d.para("prem-0", 1, "Harbour Logistics Inc. Shift Premium Schedule 2024", role="title")
    d.para("prem-1", 1, "Shift premiums apply to warehouse staff, including the Warehouse "
                        "Supervisor role.", top=1.0)
    d.para("prem-2", 1, "Effective date: 2024-01-01", top=1.3)
    d.table("prem-table", 1, [
        (0, 0, "Shift", "columnHeader", 2, 1),
        (0, 1, "Premium per hour (CAD)", "columnHeader", 1, 2),
        (1, 1, "Weekday", "columnHeader", 1, 1),
        (1, 2, "Weekend", "columnHeader", 1, 1),
        (2, 0, "Evening", "rowHeader", 1, 1),
        (2, 1, "1.50", "content", 1, 1),
        (2, 2, "2.25", "content", 1, 1),
        (3, 0, "Night", "rowHeader", 1, 1),
        (3, 1, "2.75", "content", 1, 1),
        (3, 2, "3.50", "content", 1, 1),
        (4, 0, "Statutory holiday", "rowHeader", 1, 1),
        (4, 1, "4.00", "content", 1, 2),
    ])
    d.figure("prem-fig", 1, "Premium bands by shift")
    return d.build()


def _vendor():
    d = _Doc()
    d.para("ven-0", 1, "Vendor Agreement VA-2024-017", role="title")
    d.para("ven-1", 1, "This agreement between Harbour Logistics Inc. (Business Number "
                       "845123987) and Maritime Mutual Insurance takes effect on 2024-02-01.",
           top=1.0)
    d.para("ven-2", 1, "Maritime Mutual Insurance is headquartered in Halifax.", top=1.5)
    d.para("ven-3", 1, "The monthly premium is CAD 12,400.", top=1.8)
    return d.build()


def _minutes():
    d = _Doc()
    d.para("min-0", 1, "Board Minutes, March 2024", role="title")
    d.para("min-1", 1, "The board of Harbour Logistics Incorporated (BN 845123987) approved the "
                       "renewal of agreement VA-2024-017 with Maritime Mutual Insurance.", top=1.0)
    d.para("min-2", 1, "Agreement VA-2024-017 took effect on 2024-03-01.", top=1.5)
    return d.build()


def _manual():
    d = _Doc()
    d.para("man-0", 1, "Warehouse Operations Manual", role="title")
    filler = ("Staff follow the posted loading procedures, inspect pallets before moving them, "
              "record damaged goods in the shift log and keep aisles clear of obstructions. ")
    for page in range(1, 11):
        d.para(f"man-h{page}", page, f"Chapter {page}", role="sectionHeading", top=0.8)
        for index in range(3):
            d.para(f"man-{page}-{index}", page, filler * 3, top=1.2 + index * 0.6)
    d.para("man-key", 9, "The emergency assembly point for the Dartmouth warehouse is the north "
                         "parking lot.", top=3.2)
    return d.build()


DOCUMENTS = {
    "policy": ("Employment Terms and Conditions", _policy()),
    "form": ("Employment Verification Application", _form()),
    "premiums": ("Shift Premium Schedule 2024", _premiums()),
    "vendor": ("Vendor Agreement VA-2024-017", _vendor()),
    "minutes": ("Board Minutes March 2024", _minutes()),
    "manual": ("Warehouse Operations Manual", _manual()),
}
# Ingestion order; later documents can link to entities from earlier ones.
INGESTION_ORDER = ["policy", "manual", "premiums", "form", "vendor", "minutes"]

# --- answer key (written from the documents, not from model output) ----------------

TRUTH = {
    "entities": [
        # (canonical name, type, accepted aliases)
        ("Maria Chen", "person", []),
        ("Harbour Logistics Inc.", "organization", ["Harbour Logistics Incorporated"]),
        ("Warehouse Supervisor", "role", []),
        ("Dartmouth warehouse", "facility", []),
        ("2023-04-03", "date", []),
        ("Group benefits", "benefit", []),
        ("Maritime Mutual Insurance", "organization", []),
        ("Probationary period", "term", []),
        ("Two weeks' written notice", "term", []),
        ("Shift Premium Schedule 2024", "policy", []),
        ("2024-01-01", "date", []),
        ("VA-2024-017", "agreement", ["Vendor Agreement VA-2024-017"]),
        ("2024-02-01", "date", []),
        ("2024-03-01", "date", []),
        ("Halifax", "location", []),
        ("North parking lot", "location", []),
    ],
    "relationships": [
        # (subject, accepted predicates, object, expected evidence paragraph/field/cell refs)
        ("Maria Chen", {"EMPLOYED_BY", "WORKS_FOR"}, "Harbour Logistics Inc.",
         ["form-1", "form-2", "f:form:1."]),
        ("Maria Chen", {"HAS_POSITION", "HOLDS_POSITION"}, "Warehouse Supervisor",
         ["form-8"]),
        ("Maria Chen", {"WORKS_AT"}, "Dartmouth warehouse", ["form-9"]),
        ("Maria Chen", {"START_DATE"}, "2023-04-03", ["form-10"]),
        ("Maria Chen", {"ELIGIBLE_FOR"}, "Group benefits", ["f:form:3.", "pol-2"]),
        ("Group benefits", {"ADMINISTERED_BY"}, "Maritime Mutual Insurance", ["pol-8"]),
        ("Harbour Logistics Inc.", {"NOTICE_PERIOD", "MUST_PROVIDE"},
         "Two weeks' written notice", ["pol-6"]),
        ("Harbour Logistics Inc.", {"HAS_PROBATION", "PROBATION_PERIOD"},
         "Probationary period", ["pol-4"]),
        ("Shift Premium Schedule 2024", {"APPLIES_TO"}, "Warehouse Supervisor", ["prem-1"]),
        ("Shift Premium Schedule 2024", {"EFFECTIVE_DATE"}, "2024-01-01", ["prem-2"]),
        ("VA-2024-017", {"PARTY", "HAS_PARTY"}, "Harbour Logistics Inc.", ["ven-1"]),
        ("VA-2024-017", {"PARTY", "HAS_PARTY"}, "Maritime Mutual Insurance", ["ven-1", "min-1"]),
        ("VA-2024-017", {"EFFECTIVE_DATE"}, "2024-02-01", ["ven-1"]),
        ("VA-2024-017", {"EFFECTIVE_DATE"}, "2024-03-01", ["min-2"]),
        ("Maritime Mutual Insurance", {"HEADQUARTERED_IN"}, "Halifax", ["ven-2"]),
        ("Dartmouth warehouse", {"ASSEMBLY_POINT"}, "North parking lot", ["man-key"]),
    ],
    # Two statements that conflict (each side a set of acceptable evidence refs).
    "contradictions": [({"ven-1"}, {"min-2"})],
    "exact_questions": [
        {"id": "E1", "question": "Is the position permanent?", "answer": "No",
         "evidence": "f:form:2."},
        {"id": "E2", "question": "Is the applicant currently employed by this employer?",
         "answer": "Yes", "evidence": "f:form:1."},
        {"id": "E3", "question": "What is the weekend premium for the night shift?",
         "answer": "3.50", "evidence": "c:prem-table:3:2"},
        {"id": "E4", "question": "What is the weekday premium for a statutory holiday?",
         "answer": "4.00", "evidence": "c:prem-table:4:1"},
    ],
    "multi_hop_questions": [
        {"id": "M1", "question": "Who administers the group benefits?",
         "answer": "Maritime Mutual Insurance", "hops": 1},
        {"id": "M2", "question": "Where is the emergency assembly point for the warehouse where "
                                 "Maria Chen works?", "answer": "North parking lot", "hops": 2},
        {"id": "M3", "question": "Which insurer administers the benefits Maria Chen is eligible "
                                 "for?", "answer": "Maritime Mutual Insurance", "hops": 2},
        {"id": "M4", "question": "When did agreement VA-2024-017 take effect?",
         "answer": "CONFLICT", "values": {"2024-02-01", "2024-03-01"}, "hops": 1},
        {"id": "M5", "question": "What termination notice must Harbour Logistics Incorporated "
                                 "give?", "answer": "Two weeks' written notice", "hops": 1},
        {"id": "M6", "question": "Is Maritime Mutual Insurance a subsidiary of Harbour Logistics "
                                 "Inc.?", "answer": "NOT_ESTABLISHED", "hops": 1},
    ],
}

# Recorded retrieval-planner decisions (stand-ins for the agentic planner's
# structured output): seed entities and the predicate to follow at each hop.
PLANNER_DECISIONS = {
    "M1": {"seeds": ["Group benefits"], "hops": [["ADMINISTERED_BY"]]},
    "M2": {"seeds": ["Maria Chen"], "hops": [["WORKS_AT"], ["ASSEMBLY_POINT"]]},
    "M3": {"seeds": ["Maria Chen"], "hops": [["ELIGIBLE_FOR"], ["ADMINISTERED_BY"]]},
    "M4": {"seeds": ["VA-2024-017"], "hops": [["EFFECTIVE_DATE"]]},
    "M5": {"seeds": ["Harbour Logistics Incorporated"], "hops": [["NOTICE_PERIOD", "MUST_PROVIDE"]]},
    "M6": {"seeds": ["Maritime Mutual Insurance"], "hops": [["SUBSIDIARY_OF"]], "boolean": True},
}

# --- recorded model readings (one per document) --------------------------------------
# Evidence references: "p:<paragraph id>", "c:<table>:<row>:<col>", "f:<doc>:<question prefix>",
# "t:<table id>" (the table object). Each mistake is labelled.

MODEL_READINGS = {
    "policy": {
        "document_type": "Employment terms and conditions",
        "concepts": ["Hours of work", "Probation", "Termination", "Benefits"],
        "entities": [
            {"id": "hl", "name": "Harbour Logistics Inc.", "type": "Organization",
             "evidence": ["p:pol-0", "p:pol-6"]},
            {"id": "gb", "name": "Group benefits", "type": "Benefit", "evidence": ["p:pol-2", "p:pol-8"]},
            {"id": "mm", "name": "Maritime Mutual Insurance", "type": "Organization",
             "evidence": ["p:pol-8"]},
            {"id": "pp", "name": "Probationary period", "type": "Term", "evidence": ["p:pol-4"]},
            {"id": "notice", "name": "Two weeks' written notice", "type": "Term",
             "evidence": ["p:pol-6"]},
            # Mistake: not mentioned anywhere in the document.
            {"id": "code", "name": "Nova Scotia Labour Standards Code", "type": "Law",
             "evidence": ["p:pol-6"]},
        ],
        "relationships": [
            {"s": "gb", "p": "ADMINISTERED_BY", "o": "mm", "kind": "observed",
             "evidence": ["p:pol-8"], "confidence": 0.95},
            {"s": "hl", "p": "NOTICE_PERIOD", "o": "notice", "kind": "observed",
             "evidence": ["p:pol-6"], "confidence": 0.9},
            # Correctly derived: the probation section never names the employer.
            {"s": "hl", "p": "HAS_PROBATION", "o": "pp", "kind": "derived",
             "evidence": ["p:pol-0", "p:pol-4"], "confidence": 0.8},
            # Mistake: unsupported link to the invented law.
            {"s": "notice", "p": "GOVERNED_BY", "o": "code", "kind": "observed",
             "evidence": ["p:pol-6"], "confidence": 0.7},
        ],
    },
    "manual": {
        "document_type": "Operations manual",
        "concepts": ["Warehouse procedures", "Emergency assembly"],
        "entities": [
            {"id": "dw", "name": "Dartmouth warehouse", "type": "Facility", "evidence": ["p:man-key"]},
            {"id": "np", "name": "north parking lot", "type": "Location", "evidence": ["p:man-key"]},
        ],
        "relationships": [
            {"s": "dw", "p": "ASSEMBLY_POINT", "o": "np", "kind": "observed",
             "evidence": ["p:man-key"], "confidence": 0.95},
        ],
    },
    "premiums": {
        "document_type": "Pay schedule",
        "concepts": ["Shift premiums"],
        "entities": [
            {"id": "sched", "name": "Shift Premium Schedule 2024", "type": "Policy",
             "evidence": ["p:prem-0"]},
            {"id": "ws", "name": "Warehouse Supervisor", "type": "Role", "evidence": ["p:prem-1"]},
            {"id": "eff", "name": "2024-01-01", "type": "Date", "evidence": ["p:prem-2"]},
            {"id": "hl", "name": "Harbour Logistics Inc.", "type": "Organization",
             "evidence": ["p:prem-0"]},
        ],
        "relationships": [
            # Mistake (overstated): the sentence names "Shift premiums", not the schedule.
            {"s": "sched", "p": "APPLIES_TO", "o": "ws", "kind": "observed",
             "evidence": ["p:prem-0", "p:prem-1"], "confidence": 0.85},
            {"s": "sched", "p": "EFFECTIVE_DATE", "o": "eff", "kind": "derived",
             "evidence": ["p:prem-0", "p:prem-2"], "confidence": 0.9},
        ],
    },
    "form": {
        "document_type": "Employment verification form",
        "concepts": ["Applicant", "Employment status", "Employment details"],
        "entities": [
            {"id": "mc", "name": "Maria Chen", "type": "Person", "evidence": ["p:form-1"]},
            {"id": "hl", "name": "Harbour Logistics Inc.", "type": "Organization",
             "identifiers": {"business_number": "845123987"}, "evidence": ["p:form-2", "p:form-3"]},
            {"id": "ws", "name": "Warehouse Supervisor", "type": "Role", "evidence": ["p:form-8"]},
            {"id": "dw", "name": "Dartmouth warehouse", "type": "Facility", "evidence": ["p:form-9"]},
            {"id": "start", "name": "2023-04-03", "type": "Date", "evidence": ["p:form-10"]},
            {"id": "yes", "name": "Yes", "type": "Value", "evidence": ["f:form:2."]},
            {"id": "perm", "name": "Permanent", "type": "EmploymentType",
             "evidence": ["f:form:2."]},
            # Mistake: no manager is mentioned.
            {"id": "mgr", "name": "Warehouse Manager", "type": "Role", "evidence": ["p:form-8"]},
        ],
        "relationships": [
            {"s": "mc", "p": "EMPLOYED_BY", "o": "hl", "kind": "derived",
             "evidence": ["p:form-1", "p:form-2", "f:form:1."], "confidence": 0.9},
            # Mistake (overstated): two separate lines, not one statement.
            {"s": "mc", "p": "HAS_POSITION", "o": "ws", "kind": "observed",
             "evidence": ["p:form-1", "p:form-8"], "confidence": 0.9},
            {"s": "mc", "p": "WORKS_AT", "o": "dw", "kind": "derived",
             "evidence": ["p:form-1", "p:form-9"], "confidence": 0.9},
            {"s": "mc", "p": "START_DATE", "o": "start", "kind": "derived",
             "evidence": ["p:form-1", "p:form-10"], "confidence": 0.9},
            # Cross-document derivation through an existing entity.
            {"s": "mc", "p": "ELIGIBLE_FOR", "o": "existing:benefit:name:group benefits",
             "kind": "derived", "evidence": ["f:form:3.", "p:pol-2"], "confidence": 0.8},
            # Mistake: misreads question 2 (the extracted answer is No).
            {"s": "perm", "p": "ANSWER", "o": "yes", "kind": "observed",
             "evidence": ["f:form:2."], "confidence": 0.6},
            # Mistake the validator cannot see: the field's answer is No, but the
            # claim names no option label, so only predicate semantics reveal it.
            {"s": "mc", "p": "EMPLOYMENT_TYPE", "o": "perm", "kind": "observed",
             "evidence": ["f:form:2."], "confidence": 0.55},
            {"s": "mc", "p": "REPORTS_TO", "o": "mgr", "kind": "hypothesis",
             "evidence": ["p:form-8"], "confidence": 0.4},
        ],
        "form_readings": [
            {"field": "f:form:1.", "selected_option": "Yes"},
            {"field": "f:form:2.", "selected_option": "Yes"},  # Mistake: it is No.
            {"field": "f:form:3.", "selected_option": "Yes"},
        ],
    },
    "vendor": {
        "document_type": "Vendor agreement",
        "concepts": ["Benefits insurance agreement"],
        "entities": [
            {"id": "va", "name": "Vendor Agreement VA-2024-017", "type": "Agreement",
             "aliases": ["VA-2024-017"], "identifiers": {"agreement_number": "VA-2024-017"},
             "evidence": ["p:ven-0"]},
            {"id": "hl", "name": "Harbour Logistics Inc.", "type": "Organization",
             "identifiers": {"business_number": "845123987"}, "evidence": ["p:ven-1"]},
            {"id": "mm", "name": "Maritime Mutual Insurance", "type": "Organization",
             "evidence": ["p:ven-1", "p:ven-2"]},
            {"id": "d1", "name": "2024-02-01", "type": "Date", "evidence": ["p:ven-1"]},
            {"id": "hfx", "name": "Halifax", "type": "Location", "evidence": ["p:ven-2"]},
        ],
        "relationships": [
            {"s": "va", "p": "HAS_PARTY", "o": "hl", "kind": "derived",
             "evidence": ["p:ven-0", "p:ven-1"], "confidence": 0.9},
            {"s": "va", "p": "HAS_PARTY", "o": "mm", "kind": "derived",
             "evidence": ["p:ven-0", "p:ven-1"], "confidence": 0.9},
            {"s": "va", "p": "EFFECTIVE_DATE", "o": "d1", "kind": "derived",
             "evidence": ["p:ven-0", "p:ven-1"], "confidence": 0.9},
            # Mistake: wrong citation (the headquarters sentence is ven-2).
            {"s": "mm", "p": "HEADQUARTERED_IN", "o": "hfx", "kind": "observed",
             "evidence": ["p:ven-1"], "confidence": 0.9},
            # Speculation, correctly labelled a hypothesis.
            {"s": "mm", "p": "SUBSIDIARY_OF", "o": "hl", "kind": "hypothesis",
             "evidence": ["p:ven-1"], "confidence": 0.35},
        ],
    },
    "minutes": {
        "document_type": "Board minutes",
        "concepts": ["Agreement renewal"],
        "entities": [
            {"id": "hl", "name": "Harbour Logistics Incorporated", "type": "Organization",
             "identifiers": {"business_number": "845123987"}, "evidence": ["p:min-1"]},
            {"id": "va", "name": "VA-2024-017", "type": "Agreement",
             "identifiers": {"agreement_number": "VA-2024-017"}, "evidence": ["p:min-1", "p:min-2"]},
            {"id": "mm", "name": "Maritime Mutual Insurance", "type": "Organization",
             "evidence": ["p:min-1"]},
            {"id": "d2", "name": "2024-03-01", "type": "Date", "evidence": ["p:min-2"]},
        ],
        "relationships": [
            {"s": "va", "p": "HAS_PARTY", "o": "mm", "kind": "observed",
             "evidence": ["p:min-1"], "confidence": 0.9},
            {"s": "va", "p": "EFFECTIVE_DATE", "o": "d2", "kind": "observed",
             "evidence": ["p:min-2"], "confidence": 0.9},
        ],
        "contradictions": [
            {"description": "The agreement's effective date differs between the agreement and "
                            "the board minutes.", "evidence": ["p:min-2", "p:ven-1"]},
        ],
    },
}
