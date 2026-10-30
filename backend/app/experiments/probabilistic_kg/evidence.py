"""Evidence index built from the existing structural projection.

Evidence IDs are exactly the reference-object IDs dev writes to Neo4j, so a
semantic claim's SUPPORTED_BY link lands on the same structural node and the
same Chroma passage. Each record joins that ID back to the Azure DI element it
came from (text, page, span, regions, confidence), so nothing is paraphrased.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from backend.app.services.chunking import split_structured
from backend.app.services.document_processing import with_heading_sections


@dataclass
class Evidence:
    evidence_id: str
    document_id: str
    label: str  # Paragraph, TableCell, FormField, SelectionMark, Chunk, Section, Table, Figure
    page: int | None
    text: str
    source_element_id: str | None = None
    span: dict | None = None
    bounding_regions: list = field(default_factory=list)
    confidence: float | None = None
    extra: dict = field(default_factory=dict)


class RecordingStructureGraph:
    """Captures what the structural projection would write to Neo4j."""

    def __init__(self):
        self.documents, self.nodes, self.edges = {}, {}, []

    def upsert_document(self, identifier, title, kind, **properties):
        self.documents[identifier] = {"title": title, "kind": kind, **properties}

    def upsert_pages(self, doc_id, pages, version=1):
        for page in pages:
            self.nodes[f"{doc_id}:page:{page}"] = {
                "label": "Page", "document_id": doc_id, "page": page, "version": version,
            }
            self.edges.append((doc_id, "HAS_PAGE", f"{doc_id}:page:{page}"))

    def upsert_reference_objects(self, doc_id, items):
        for item in items:
            self.nodes[item["object_id"]] = {
                "label": item["object_type"], "document_id": doc_id, **{
                    key: item.get(key) for key in (
                        "page", "position", "source_element_id", "table_row", "table_column",
                        "bounding_regions", "description", "state_flag", "confidence", "role",
                        "span_offset", "span_length", "azure_role", "version",
                    )
                },
            }
            if item["object_type"] != "SelectionMark":
                label = item["object_type"].upper()
                self.edges.append((f"{doc_id}:page:{item.get('page')}", f"HAS_{label}",
                                   item["object_id"]))

    def link_reference_objects_many(self, links):
        for source, target, relationship, *_labels in links:
            self.edges.append((source, relationship, target))

    def link_website_document(self, *args, **kwargs):
        return {"outbound": 0, "inbound": 0}


def project_structure(document_id, title, extraction, chunk_tokens=400):
    """Run dev's structural projection unchanged and return what it wrote."""
    from backend.app.services.local_documents import LocalDocumentRepository

    graph = RecordingStructureGraph()
    owner = SimpleNamespace(settings=SimpleNamespace(chunk_tokens=chunk_tokens))
    counts = LocalDocumentRepository._publish_reference_objects(
        owner, graph, document_id, title, extraction,
    )
    return graph, counts


class EvidenceIndex:
    """All citable evidence for a set of documents, keyed by evidence ID."""

    def __init__(self):
        self.records: dict[str, Evidence] = {}
        self.structures: dict[str, RecordingStructureGraph] = {}
        self.extractions: dict[str, dict] = {}
        self.titles: dict[str, str] = {}

    def add_document(self, document_id, title, extraction, chunk_tokens=400):
        extraction = with_heading_sections(extraction)
        graph, _counts = project_structure(document_id, title, extraction, chunk_tokens)
        self.structures[document_id] = graph
        self.extractions[document_id] = extraction
        self.titles[document_id] = title
        objects = {
            item["object_id"]: item
            for item in split_structured(document_id, extraction, chunk_tokens, 1)
        }
        paragraphs = {str(p.get("id")): p for p in extraction.get("paragraphs", [])}
        tables = {str(t.get("id")): t for t in extraction.get("tables", [])}
        marks = {
            str(mark.get("id")): mark
            for page in extraction.get("pages", [])
            for mark in page.get("selection_marks") or []
        }
        option_by_mark = {
            option["mark_id"]: (item["object_id"], option)
            for item in objects.values() if item["object_type"] == "FormField"
            for option in _field_options(item)
        }
        for node_id, node in graph.nodes.items():
            label = node["label"]
            if label == "Page":
                continue
            record = Evidence(
                evidence_id=node_id, document_id=document_id, label=label,
                page=node.get("page"), text="",
                source_element_id=node.get("source_element_id"),
                bounding_regions=node.get("bounding_regions") or [],
            )
            if node_id in objects:
                item = objects[node_id]
                record.text = item.get("text", "")
                if label == "FormField":
                    record.confidence = item.get("field_confidence")
                    record.extra = {
                        "question": item.get("question"),
                        "options": item.get("options", []),
                        "selected_option": item.get("selected_option"),
                        "field_status": item.get("field_status"),
                        "ambiguity_reason": item.get("ambiguity_reason"),
                        "association_method": item.get("association_method"),
                        "marks": item.get("marks", []),
                    }
                elif label == "Table":
                    record.extra = {"table_id": item.get("table_id")}
            elif label == "Paragraph":
                paragraph = paragraphs.get(str(node.get("source_element_id")), {})
                record.text = paragraph.get("text", "")
                record.span = (paragraph.get("spans") or [None])[0]
                record.confidence = paragraph.get("confidence")
                record.extra = {"role": paragraph.get("role"), "position": paragraph.get("position")}
            elif label == "TableCell":
                table = tables.get(str(node.get("source_element_id")), {})
                row, column = node.get("table_row"), node.get("table_column")
                cell = _cell(table, row, column)
                record.text = cell.get("text", "")
                record.span = (cell.get("spans") or [None])[0]
                record.confidence = cell.get("confidence")
                columns = range(column, column + int(cell.get("column_span", 1) or 1))
                rows = range(row, row + int(cell.get("row_span", 1) or 1))
                column_headers = [_headers(table, "columnHeader", value) for value in columns]
                row_headers = [_headers(table, "rowHeader", value) for value in rows]
                record.extra = {
                    "table_id": table.get("id"), "row": row, "column": column,
                    "row_span": cell.get("row_span", 1), "column_span": cell.get("column_span", 1),
                    "kind": cell.get("kind", "content"),
                    # A merged cell answers for every column/row it covers.
                    "column_headers": column_headers, "row_headers": row_headers,
                    "column_header": " / ".join(column_headers),
                    "row_header": " / ".join(row_headers),
                }
            elif label == "SelectionMark":
                mark_id = node.get("source_element_id")
                field_id, option = option_by_mark.get(mark_id, (None, {}))
                mark = marks.get(str(mark_id), {})
                record.text = str(option.get("label") or "")
                record.confidence = node.get("confidence")
                record.span = mark.get("span")
                record.bounding_regions = mark.get("bounding_regions") or record.bounding_regions
                record.extra = {
                    "form_field_id": field_id, "state": node.get("state_flag"),
                }
            elif label == "Figure":
                record.text = node.get("description") or ""
            self.records[node_id] = record
        return graph

    def get(self, evidence_id):
        return self.records.get(evidence_id)

    def for_document(self, document_id):
        return [r for r in self.records.values() if r.document_id == document_id]

    def form_fields(self, document_id=None):
        return [
            r for r in self.records.values()
            if r.label == "FormField" and (document_id is None or r.document_id == document_id)
        ]


def _cell(table, row, column):
    for cell in table.get("cells", []):
        if cell.get("row") == row and cell.get("column") == column:
            return cell
    return {}


def _headers(table, kind, index):
    """Header text covering one column (top-down) or one row, honouring merged cells."""
    axis, span = ("column", "column_span") if kind == "columnHeader" else ("row", "row_span")
    order = "row" if kind == "columnHeader" else "column"
    cells = [
        cell for cell in table.get("cells", [])
        if cell.get("kind") == kind
        and cell[axis] <= index < cell[axis] + int(cell.get(span, 1) or 1)
    ]
    if not cells and kind == "rowHeader":
        cells = [cell for cell in table.get("cells", []) if cell["row"] == index
                 and cell["column"] == 0]
    return " / ".join(cell.get("text", "") for cell in sorted(cells, key=lambda c: c[order]))


def _field_options(item):
    labels = item.get("options", [])
    marks = item.get("marks", [])
    return [
        {"mark_id": mark["mark_id"], "label": labels[index] if index < len(labels) else ""}
        for index, mark in enumerate(marks)
    ]
