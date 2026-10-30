"""Form check boxes: one readable representation, and fields that keep marks with their questions.

Azure Document Intelligence reports selection marks as ``:selected:`` /
``:unselected:`` tokens in paragraph text and as ``☒`` / ``☐`` in Markdown.
Everything a model or a person reads uses ``[x]`` / ``[ ]`` instead.

A form field joins a question to its options and their marks. Marks are
associated by span first (the mark sits inside the question's own paragraph
or table row) and by page geometry as a fallback (the nearest unanswered
question on the same page). Fillable PDFs supply their fields directly. An
uncertain association, a low-confidence mark, or zero or several selected
options make the field ``ambiguous``: no answer is chosen for it.
"""

from __future__ import annotations

import re

MARK_TOKEN = re.compile(r":selected:|:unselected:|[☒☑☐]")
SELECTED_TOKENS = {":selected:", "☒", "☑"}
MIN_MARK_CONFIDENCE = 0.6
# Page geometry is in the extraction's units (inches for PDFs).
GEOMETRY_TIE = 0.05
GEOMETRY_MAX_DISTANCE = 1.0
# A short label ending in ":" ("Gender:") owns its marks; longer prompt text
# without a question is an instruction, so its marks belong to a nearby question.
MAX_LABEL_WORDS = 6
SKIPPED_ROLES = {"pageHeader", "pageFooter", "pageNumber"}


def readable_marks(text):
    """Selection marks as ``[x]`` (selected) and ``[ ]`` (unselected)."""
    return MARK_TOKEN.sub(
        lambda match: "[x]" if match.group(0) in SELECTED_TOKENS else "[ ]", str(text or "")
    )


def _box(regions):
    points = [point for region in regions or [] for point in region.get("polygon") or []]
    if not points:
        return None
    xs, ys = [point["x"] for point in points], [point["y"] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _center_y(box):
    return (box[1] + box[3]) / 2


def _question_text(text):
    """The text up to and including the first question mark, else the text itself."""
    head, mark, _ = str(text).partition("?")
    return (head + mark).strip() if mark else str(text).strip()


def _short_label(text):
    return re.split(r"(?<=[.?!:])\s", str(text).strip(), maxsplit=1)[0][:80].strip()


def parse_marks(text):
    """Split text with inline mark tokens into (prompt, [(selected, label)]) or None."""
    tokens = MARK_TOKEN.findall(str(text or ""))
    if not tokens:
        return None
    parts = MARK_TOKEN.split(str(text))
    selected = [token in SELECTED_TOKENS for token in tokens]
    if parts[-1].strip():
        # "☒ Yes ☐ No": each mark precedes its label.
        labels = [_short_label(part) for part in parts[1:]]
        prompt = parts[0].strip()
    else:
        # "Yes ☒ No ☐": each label precedes its mark.
        head = parts[0]
        split_at = max(head.rfind("?"), head.rfind(":"))
        prompt, first = (
            (head[: split_at + 1], head[split_at + 1 :]) if split_at >= 0 else ("", head)
        )
        labels = [_short_label(first), *(_short_label(part) for part in parts[1:-1])]
        prompt = prompt.strip()
    labels = [label or f"option {index + 1}" for index, label in enumerate(labels)]
    return prompt, list(zip(selected, labels, strict=True))


def _owns_marks(prompt):
    """Whether a block's own prompt is the question its marks answer."""
    if "?" in prompt or re.match(r"\(?\d{1,3}[.)]\s", prompt):
        return True  # a question, or a numbered form item
    return bool(prompt) and prompt.endswith(":") and len(prompt.split()) <= MAX_LABEL_WORDS


def _page_marks(extraction):
    marks = {}
    for page in extraction.get("pages", []):
        for mark in page.get("selection_marks") or []:
            marks.setdefault(mark.get("page", page.get("page")), []).append(mark)
    return marks


def _marks_in_span(page_marks, spans, count):
    """Saved Azure selection marks inside a block's spans, if they match its tokens."""
    found = [
        mark
        for mark in page_marks
        if any(
            span["offset"]
            <= (mark.get("span") or {}).get("offset", -1)
            < span["offset"] + span["length"]
            for span in spans or []
        )
    ]
    found.sort(key=lambda mark: mark["span"]["offset"])
    return found if len(found) == count else []


def _blocks(extraction):
    """Paragraphs and table rows that carry selection marks, with their geometry."""
    page_marks = _page_marks(extraction)
    blocks = []
    for paragraph in extraction.get("paragraphs", []):
        if paragraph.get("role") in SKIPPED_ROLES:
            continue
        parsed = parse_marks(paragraph.get("text"))
        if not parsed:
            continue
        prompt, options = parsed
        page = paragraph.get("page")
        blocks.append(
            {
                "source_id": paragraph.get("id"),
                "page": page,
                "prompt": prompt,
                "options": options,
                "box": _box(paragraph.get("bounding_regions")),
                "marks": _marks_in_span(
                    page_marks.get(page, []), paragraph.get("spans"), len(options)
                ),
            }
        )
    for table in extraction.get("tables", []):
        cells = sorted(table.get("cells", []), key=lambda cell: (cell["row"], cell["column"]))
        header = {cell["column"]: cell.get("text", "") for cell in cells if cell["row"] == 0}
        rows = {}
        for cell in cells:
            rows.setdefault(cell["row"], []).append(cell)
        for row_index, row in rows.items():
            options, prompt, row_marks, regions = [], "", [], []
            for cell in row:
                parsed = parse_marks(cell.get("text"))
                regions.extend(cell.get("bounding_regions") or [])
                if not parsed:
                    prompt = prompt or str(cell.get("text") or "").strip()
                    continue
                cell_prompt, cell_options = parsed
                for selected, label in cell_options:
                    if label.startswith("option ") and header.get(cell["column"]):
                        label = _short_label(header[cell["column"]])
                    options.append((selected, label))
                prompt = prompt or cell_prompt
                row_marks.extend(
                    _marks_in_span(
                        page_marks.get(table.get("page"), []), cell.get("spans"), len(cell_options)
                    )
                )
            if options and row_index != 0:
                # A table row's label cell is its question, whatever its wording.
                blocks.append(
                    {
                        "source_id": f"{table.get('id')}-row-{row_index}",
                        "page": table.get("page"),
                        "prompt": prompt,
                        "options": options,
                        "box": _box(regions),
                        "marks": row_marks if len(row_marks) == len(options) else [],
                        "owned": True,
                    }
                )
    return blocks


def _unanswered_questions(extraction, page):
    questions = []
    for paragraph in extraction.get("paragraphs", []):
        if (
            paragraph.get("page") == page
            and paragraph.get("role") not in SKIPPED_ROLES
            and "?" in str(paragraph.get("text"))
            and not MARK_TOKEN.search(str(paragraph.get("text")))
            and _box(paragraph.get("bounding_regions"))
        ):
            questions.append(paragraph)
    return questions


def form_fields(extraction):
    """Form fields with their question, options, marks and an answer only when certain."""
    fields, claims = [], {}
    for block in _blocks(extraction):
        question, method, reason = block["prompt"], "span", None
        options = block["options"]
        if (
            len(options) == 1
            and not block.get("owned")
            and not _owns_marks(block["prompt"])
            and not options[0][1].startswith("option ")
        ):
            # A lone labelled box ("[x] A detailed position description") is a
            # checklist item: the label is the question, checked or not the answer.
            question = " ".join(filter(None, [block["prompt"], options[0][1]]))
            block["options"] = [(options[0][0], "checked")]
            block["checklist"] = True
        elif not block.get("owned") and not _owns_marks(block["prompt"]):
            method = "geometry"
            mark_boxes = [_box(mark.get("bounding_regions")) for mark in block["marks"]]
            mark_boxes = [box for box in mark_boxes if box]
            anchor = (
                sum(_center_y(box) for box in mark_boxes) / len(mark_boxes)
                if mark_boxes
                else _center_y(block["box"])
                if block["box"]
                else None
            )
            ranked = (
                sorted(
                    (
                        (abs(anchor - _center_y(_box(candidate["bounding_regions"]))), candidate)
                        for candidate in _unanswered_questions(extraction, block["page"])
                    ),
                    key=lambda pair: pair[0],
                )
                if anchor is not None
                else []
            )
            ranked = [item for item in ranked if item[0] <= GEOMETRY_MAX_DISTANCE]
            if not ranked:
                reason = "no_question_found"
            elif len(ranked) > 1 and ranked[1][0] - ranked[0][0] < GEOMETRY_TIE:
                reason = "association_tie"
            else:
                distance, candidate = ranked[0]
                question = _question_text(candidate["text"])
                previous = claims.get(candidate["id"])
                if previous and previous[0] <= distance:
                    reason = (
                        "association_tie"
                        if previous[0] == distance
                        else "question_already_answered"
                    )
                else:
                    if previous:
                        fields[previous[1]].update(
                            status="ambiguous",
                            ambiguity_reason="question_already_answered",
                            selected_option=None,
                        )
                    claims[candidate["id"]] = (distance, len(fields))
                    block["question_source_id"] = candidate["id"]
        confidences = [
            mark.get("confidence") for mark in block["marks"] if mark.get("confidence") is not None
        ]
        confidence = min(confidences) if confidences else None
        selected = [label for is_selected, label in block["options"] if is_selected]
        if reason is None and confidence is not None and confidence < MIN_MARK_CONFIDENCE:
            reason = "low_confidence"
        if block.get("checklist") and not selected:
            selected = ["unchecked"]
        if reason is None and not selected:
            reason = "none_selected"
        if reason is None and len(selected) > 1:
            reason = "multiple_selected"
        if not question.strip() and all(
            label.startswith("option ") for _, label in block["options"]
        ):
            # A lone, unlabelled mark with no question nearby: nothing to report as a
            # field. It stays readable in its chunk text.
            continue
        mark_ids = [mark.get("id") for mark in block["marks"]] or [
            f"{block['source_id']}-mark-{index}" for index in range(len(block["options"]))
        ]
        fields.append(
            {
                "id": f"formfield-{block['page']}-{len(fields)}",
                "page": block["page"],
                "question": _question_text(question),
                "options": [
                    {
                        "label": label,
                        "selected": is_selected,
                        "mark_id": mark_id,
                        "confidence": block["marks"][index].get("confidence")
                        if block["marks"]
                        else None,
                    }
                    for index, ((is_selected, label), mark_id) in enumerate(
                        zip(block["options"], mark_ids, strict=True)
                    )
                ],
                "selected_option": selected[0] if reason is None else None,
                "status": "answered" if reason is None else "ambiguous",
                "ambiguity_reason": reason,
                "association_method": method,
                "confidence": confidence,
                "mark_ids": mark_ids,
                "source_id": block["source_id"],
                "question_source_id": block.get("question_source_id", block["source_id"]),
            }
        )
    for ordinal, item in enumerate(extraction.get("form_fields") or []):
        fields.append(_acroform_field(item, ordinal))
    return fields


def _acroform_field(item, ordinal):
    kind, value = item.get("kind"), item.get("value")
    options = item.get("options") or []
    if kind in {"checkbox", "radio"}:
        chosen = value if value and value != "Off" else None
        option_rows = [
            {"label": option, "selected": option == chosen, "mark_id": f"{item['id']}-{option}"}
            for option in options
        ] or [{"label": "checked", "selected": bool(chosen), "mark_id": item["id"]}]
        selected = [row["label"] for row in option_rows if row["selected"]]
        status = "answered" if len(selected) == 1 or kind == "checkbox" else "ambiguous"
        selected_option = (
            selected[0] if len(selected) == 1 else ("unchecked" if kind == "checkbox" else None)
        )
        reason = None if status == "answered" else "none_selected"
    else:
        option_rows = (
            [{"label": str(value), "selected": True, "mark_id": item["id"]}] if value else []
        )
        selected_option = str(value) if value else None
        status, reason = ("answered", None) if value else ("ambiguous", "no_value")
    return {
        "id": f"formfield-acroform-{item.get('page')}-{ordinal}",
        "page": item.get("page"),
        "question": item.get("label") or item.get("name") or "form field",
        "options": option_rows,
        "selected_option": selected_option,
        "status": status,
        "ambiguity_reason": reason,
        "association_method": "acroform",
        "confidence": None,
        "mark_ids": [row["mark_id"] for row in option_rows],
        "source_id": item["id"],
        "question_source_id": item["id"],
    }


def render_field(field):
    """One line keeping a question with every option and its mark."""
    options = " / ".join(
        f"{option['label']} [{'x' if option['selected'] else ' '}]" for option in field["options"]
    )
    outcome = (
        f"selected: {field['selected_option']}"
        if field["status"] == "answered"
        else f"selection not determinable: {field['ambiguity_reason'].replace('_', ' ')}"
    )
    return f"{field['question']} — {options} ({outcome})"


def acroform_fields(source):
    """Checkbox, radio, text and choice fields of a fillable PDF, read locally with pypdf."""
    from pypdf import PdfReader

    reader = PdfReader(str(source)) if not hasattr(source, "read") else PdfReader(source)
    if "/AcroForm" not in reader.trailer["/Root"]:
        return []
    fields, seen = [], {}
    for page_number, page in enumerate(reader.pages, start=1):
        for annotation in page.get("/Annots") or []:
            widget = annotation.get_object()
            if widget.get("/Subtype") != "/Widget":
                continue
            field = widget if "/T" in widget else (widget.get("/Parent") or widget).get_object()
            name = _field_name(field)
            kind_code = field.get("/FT") or widget.get("/FT")
            flags = int(field.get("/Ff", 0) or 0)
            kind = {"/Tx": "text", "/Ch": "choice"}.get(kind_code) or (
                "radio" if flags & (1 << 15) else "checkbox" if kind_code == "/Btn" else "other"
            )
            states = (
                [
                    str(state).lstrip("/")
                    for state in (widget.get("/AP", {}).get("/N", {}) or {})
                    if str(state) != "/Off"
                ]
                if kind in {"checkbox", "radio"}
                else [str(option) for option in field.get("/Opt", []) or []]
            )
            value = field.get("/V")
            value = str(value).lstrip("/") if value is not None else None
            if kind == "checkbox" and value is None:
                value = str(widget.get("/AS", "/Off")).lstrip("/")
            rect = [round(float(number), 2) for number in widget.get("/Rect", [])]
            if name in seen:
                existing = fields[seen[name]]
                existing["options"] = list(dict.fromkeys([*existing["options"], *states]))
                continue
            seen[name] = len(fields)
            fields.append(
                {
                    "id": f"acroform-{page_number}-{len(fields)}",
                    "page": page_number,
                    "name": name,
                    "label": str(field.get("/TU") or "").strip() or None,
                    "kind": kind,
                    "value": value,
                    "options": states,
                    "widget_rect": rect,
                }
            )
    return fields


def _field_name(field):
    parts = []
    while field is not None:
        if "/T" in field:
            parts.append(str(field["/T"]))
        parent = field.get("/Parent")
        field = parent.get_object() if parent is not None else None
    return ".".join(reversed(parts))
