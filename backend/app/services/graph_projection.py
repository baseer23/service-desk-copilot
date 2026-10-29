"""Deterministic meaning for the structural graph projection.

Everything here is read directly from the saved Azure Document Intelligence
extraction: question text, option labels and their marks, table headers
(merged cells included), sections and their page ranges, reading-order
links. Nothing is inferred by a model.

Content policy (``GRAPH_CONTENT_POLICY``):

* ``references`` (default): structure and authored labels only — form
  questions, section titles, captions, shapes, spans, kinds, data types,
  states. Values stay local and are explicitly cleared from the graph: which
  option is selected, option labels next to their marks, and all table text
  (cells and headers alike, because Azure sometimes marks filled-in values as
  header cells).
* ``structured_values``: also stores those exact values on the nodes, so an
  exact question is answered from Neo4j alone. Enable it only where storing
  document values in the configured Neo4j (for example Aura) is authorized.
"""

from __future__ import annotations

import re

PROJECTION_VERSION = 2
CONTENT_POLICIES = {"references", "structured_values"}
VALUE_KEYS = (
    "selected_option", "option_label", "value", "row_header", "row_headers", "column_header",
    "column_headers", "header_text",
)
SELECTION_TOKENS = {":selected:": "selected", ":unselected:": "unselected",
                    "☒": "selected", "☑": "selected", "☐": "unselected"}

_CURRENCY = re.compile(r"^\s*(?P<unit>CAD|USD|EUR|GBP|\$|€|£)\s?(?P<n>[-+]?\d[\d,]*(?:\.\d+)?)\s*$"
                       r"|^\s*(?P<n2>[-+]?\d[\d,]*(?:\.\d+)?)\s?(?P<unit2>CAD|USD|EUR|GBP)\s*$",
                       re.IGNORECASE)
_HEADER_UNIT = re.compile(r"\((CAD|USD|EUR|GBP|\$|%|hours?|days?|weeks?|km|kg)\)", re.IGNORECASE)
_PERCENT = re.compile(r"^\s*[-+]?\d+(?:\.\d+)?\s?%\s*$")
_NUMBER = re.compile(r"^\s*[-+]?\d[\d,]*(?:\.\d+)?\s*$")
_DATE = re.compile(
    r"^\s*(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.? \d{1,2},? \d{4}|"
    r"\d{1,2} (?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d{4})\s*$",
    re.IGNORECASE,
)


_KINDS = {
    "columnheader": "columnHeader", "column_header": "columnHeader",
    "rowheader": "rowHeader", "row_header": "rowHeader",
    "stubhead": "stubHead", "stub_head": "stubHead",
    "description": "description", "content": "content",
}


def cell_kind(value) -> str:
    """Azure's cell kind, also from extractions that saved the SDK enum's text
    (for example "DocumentTableCellKind.COLUMN_HEADER")."""
    raw = str(getattr(value, "value", value) or "content").rsplit(".", 1)[-1]
    return _KINDS.get(raw.casefold(), "content")


def with_cell_kinds(extraction: dict) -> dict:
    """The extraction with every table cell's kind normalised (the original is unchanged)."""
    tables = [
        {**table, "cells": [{**cell, "kind": cell_kind(cell.get("kind"))}
                            for cell in table.get("cells", [])]}
        for table in extraction.get("tables", [])
    ]
    return {**extraction, "tables": tables}


def normalize_question(text: str) -> str:
    """Comparable question text: no item number, case, extra spaces or end punctuation."""
    text = re.sub(r"^\W*\d{1,3}[.)]\s*", "", str(text or ""))
    return re.sub(r"\s+", " ", text).strip().rstrip("?:.").strip().casefold()


def value_type(text: str) -> tuple[str, str | None]:
    """A cell value's data type and unit, only when the text itself shows them."""
    value = str(text or "").strip()
    if not value:
        return "empty", None
    if value in SELECTION_TOKENS:
        return "selection_mark", None
    match = _CURRENCY.match(value)
    if match:
        unit = (match.group("unit") or match.group("unit2") or "").upper()
        return "currency", {"$": "$", "€": "EUR", "£": "GBP"}.get(unit, unit)
    if _PERCENT.match(value):
        return "percent", "%"
    if _NUMBER.match(value):
        return "number", None
    if _DATE.match(value):
        return "date", None
    return "text", None


def headers(table: dict, kind: str, index: int) -> str:
    """Header text covering one column (top-down) or one row, honouring merged cells."""
    axis, span = ("column", "column_span") if kind == "columnHeader" else ("row", "row_span")
    order = "row" if kind == "columnHeader" else "column"
    cells = [
        cell for cell in table.get("cells", [])
        if cell.get("kind") == kind
        and cell[axis] <= index < cell[axis] + int(cell.get(span, 1) or 1)
    ]
    return " / ".join(
        str(cell.get("text") or "").strip()
        for cell in sorted(cells, key=lambda c: c[order]) if str(cell.get("text") or "").strip()
    )


def table_headers(table: dict) -> tuple[list[str], list[str]]:
    columns = int(table.get("column_count") or 0) or 1 + max(
        (c["column"] + int(c.get("column_span", 1) or 1) - 1 for c in table.get("cells", [])),
        default=0,
    )
    rows = int(table.get("row_count") or 0) or 1 + max(
        (c["row"] for c in table.get("cells", [])), default=0
    )
    return (
        [headers(table, "columnHeader", index) for index in range(columns)],
        [headers(table, "rowHeader", index) for index in range(rows)],
    )


def _offset(item):
    spans = item.get("spans") or []
    return spans[0].get("offset") if spans else None


def section_layout(extracted: dict):
    """Each heading section's page range, and a lookup for the section enclosing an offset."""
    sections = {section["id"]: section for section in extracted.get("sections", [])}
    headings = []
    for paragraph in extracted.get("paragraphs", []):
        if paragraph.get("role") not in {"title", "sectionHeading"}:
            continue
        section_id = f"section-{paragraph.get('page')}-{paragraph.get('position')}"
        if section_id in sections:
            headings.append((paragraph, sections[section_id]))
    last_page = max((int(p.get("page") or 1) for p in extracted.get("pages", [])), default=1)
    ranges = {}
    for index, (_paragraph, section) in enumerate(headings):
        end = last_page
        for _later, other in headings[index + 1:]:
            if int(other.get("level", 2)) <= int(section.get("level", 2)):
                end = max(int(section["page"]), int(other["page"]))
                break
        ranges[section["id"]] = (int(section["page"]), end)

    def enclosing(page, offset):
        chosen = None
        for paragraph, section in headings:
            heading_offset = _offset(paragraph)
            if offset is not None and heading_offset is not None:
                if heading_offset <= offset:
                    chosen = section
            elif int(paragraph.get("page") or 0) <= int(page or 0):
                chosen = section
        return chosen

    return ranges, enclosing


def paragraph_chunks(objects, paragraphs):
    """Which Chunk object holds each paragraph (same page, text contained)."""
    chunks = [item for item in objects if item["object_type"] == "Chunk"]
    mapping = {}
    for paragraph in paragraphs:
        text = str(paragraph.get("text") or "").strip()
        if not text:
            continue
        for chunk in chunks:
            if chunk.get("page") == paragraph.get("page") and text in str(chunk.get("text") or ""):
                mapping[str(paragraph.get("id"))] = chunk["object_id"]
                break
    return mapping


class ProjectionPolicy:
    """Decides which meaningful values a node may carry in the graph."""

    def __init__(self, policy: str = "references"):
        if policy not in CONTENT_POLICIES:
            raise ValueError("GRAPH_CONTENT_POLICY must be references or structured_values")
        self.policy = policy

    @property
    def values_allowed(self):
        return self.policy == "structured_values"

    def gate(self, properties: dict) -> dict:
        """Clear value-bearing keys the policy does not allow (None removes them)."""
        if self.values_allowed:
            return properties
        return {**properties, **{key: None for key in VALUE_KEYS if key in properties}}

    def form_field(self, item):
        options = list(item.get("options") or [])
        answered = item.get("field_status") == "answered"
        return self.gate({
            "question": str(item.get("question") or "").strip(),
            "question_normalized": normalize_question(item.get("question")),
            "field_type": "single_choice" if len(options) > 1 else "checkbox",
            "field_status": item.get("field_status"),
            "association_method": item.get("association_method"),
            "ambiguity_reason": item.get("ambiguity_reason"),
            "option_count": len(options),
            # Only an answered field has a verified selection; ambiguity stays explicit.
            "selected_option": item.get("selected_option") if answered else None,
            "content_policy": self.policy,
        })

    def selection_mark(self, field_item, ordinal, mark):
        labels = list(field_item.get("options") or [])
        return self.gate({
            "ordinal": ordinal,
            "selected": bool(mark.get("selected")),
            "form_field_id": field_item["object_id"],
            # The label Azure placed next to this mark, never inferred from position.
            "option_label": labels[ordinal] if ordinal < len(labels) else None,
            "content_policy": self.policy,
        })

    def table(self, item, table, section=None):
        column_headers, row_headers = table_headers(table) if table else ([], [])
        return self.gate({
            "table_id": item.get("table_id"),
            "row_group": item.get("row_group"),
            "title": str(table.get("caption") or "").strip() or None if table else None,
            "caption": str(table.get("caption") or "").strip() or None if table else None,
            "row_count": table.get("row_count") if table else None,
            "column_count": table.get("column_count") if table else None,
            "column_headers": [value for value in column_headers if value],
            "row_headers": [value for value in row_headers if value],
            "content_policy": self.policy,
        })

    def table_cell(self, table, cell):
        kind = str(cell.get("kind") or "content")
        text = str(cell.get("text") or "").strip()
        column_header = " | ".join(dict.fromkeys(
            value for column in range(cell["column"],
                                      cell["column"] + int(cell.get("column_span", 1) or 1))
            if (value := headers(table, "columnHeader", column))
        ))
        row_header = " | ".join(dict.fromkeys(
            value for row in range(cell["row"], cell["row"] + int(cell.get("row_span", 1) or 1))
            if (value := headers(table, "rowHeader", row))
        ))
        data_type, unit = value_type(text) if kind == "content" else ("header", None)
        stated = _HEADER_UNIT.search(column_header or "")
        if kind == "content" and data_type == "number" and stated:
            # The unit is stated by the table's own header, e.g. "Premium (CAD)".
            unit = stated.group(1).upper() if stated.group(1) != "%" else "%"
            data_type = "percent" if unit == "%" else (
                "currency" if unit in {"CAD", "USD", "EUR", "GBP", "$"} else "quantity"
            )
        properties = {
            "table_id": table.get("id"), "kind": kind,
            "row_span": int(cell.get("row_span", 1) or 1),
            "column_span": int(cell.get("column_span", 1) or 1),
            "column_header": column_header or None,
            "row_header": row_header or None,
            "data_type": data_type, "unit": unit,
            # A check box inside a cell is stored as its state, not its token.
            "value": (SELECTION_TOKENS.get(text, text) if kind == "content" else None),
            "content_policy": self.policy,
        }
        if kind == "columnHeader":
            # Column headers are structural labels, like a form question.
            properties["header_text"] = text
        elif kind != "content":
            properties["value"] = text  # row headers and stub cells are values
        return self.gate(properties)

    def section(self, item, ranges, references=0):
        start, end = ranges.get(item.get("source_id"), (item.get("page"), item.get("page")))
        return {
            "title": str(item.get("text") or "").strip()[:300] or None,
            "page_start": start, "page_end": end, "content_references": references,
        }

    @staticmethod
    def figure(item):
        caption = str(item.get("text") or "").strip()
        return {"caption": caption[:300] or None}
