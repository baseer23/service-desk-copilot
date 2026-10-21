from __future__ import annotations

import os
import re
import hashlib
from typing import List, Dict

TOKEN_APPROX_CHARS = 4
DEFAULT_CHUNK_TOKENS = 512
DEFAULT_CHUNK_OVERLAP = 64


def approx_tokens(text: str) -> int:
    """Return a deterministic approximation of token count."""
    stripped = text.strip()
    if not stripped:
        return 0
    # Simple heuristic: mix words and characters to keep fairly stable across inputs.
    word_count = len(re.findall(r"\S+", stripped))
    char_estimate = max(1, len(stripped) // TOKEN_APPROX_CHARS)
    return max(word_count, char_estimate)


def split_text(
    text: str, chunk_tokens: int | None = None, overlap: int | None = None
) -> List[Dict[str, object]]:
    """Split raw text into chunk dictionaries with token metadata."""
    tokens = re.findall(r"\S+", text)
    if not tokens:
        return []

    chunk_size = (
        chunk_tokens
        if chunk_tokens is not None
        else _read_int_env("CHUNK_TOKENS", DEFAULT_CHUNK_TOKENS)
    )
    chunk_overlap = (
        overlap if overlap is not None else _read_int_env("CHUNK_OVERLAP", DEFAULT_CHUNK_OVERLAP)
    )

    if chunk_size <= 0:
        chunk_size = DEFAULT_CHUNK_TOKENS
    if chunk_overlap < 0:
        chunk_overlap = 0
    if chunk_overlap >= chunk_size:
        chunk_overlap = max(0, chunk_size // 2)

    chunks: List[Dict[str, object]] = []
    start = 0
    index = 0
    while start < len(tokens):
        end = min(len(tokens), start + chunk_size)
        current_tokens = tokens[start:end]
        chunk_text = " ".join(current_tokens)
        chunks.append(
            {
                "id": f"chunk-{index}",
                "text": chunk_text,
                "ord": index,
                "tokens": len(current_tokens),
            }
        )
        if end == len(tokens):
            break
        start = max(0, end - chunk_overlap)
        index += 1
    return chunks


def split_structured(
    document_id: str,
    extraction: dict,
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    extraction_version: int = 1,
):
    """Create stable, structure-aware searchable objects from a preserved extraction."""
    objects = []

    def add(kind, page, position, text, **references):
        text = str(text or "").strip()
        if not text:
            return
        stable = hashlib.sha256(
            f"{document_id}:{extraction_version}:{kind}:{page}:{position}".encode()
        ).hexdigest()[:24]
        objects.append(
            {
                "id": f"{document_id}-{kind.lower()}-{stable}",
                "object_id": f"{kind[0].upper()}{stable}",
                "object_type": kind,
                "page": page,
                "position": position,
                "text": text,
                "tokens": approx_tokens(text),
                **references,
            }
        )

    for position, section in enumerate(extraction.get("sections", [])):
        add(
            "Section",
            section.get("page"),
            position,
            section.get("title"),
            source_id=section.get("id"),
            parent_id=section.get("parent_id"),
        )

    paragraphs = extraction.get("paragraphs") or []
    if paragraphs:
        section_id = None
        buffer, start_page, start_position = [], None, None
        for paragraph in paragraphs:
            if paragraph.get("role") in {"title", "sectionHeading"}:
                if buffer:
                    add(
                        "Chunk",
                        start_page,
                        start_position,
                        "\n\n".join(buffer),
                        section_id=section_id,
                    )
                    buffer = []
                section_id = next(
                    (
                        section["id"]
                        for section in extraction.get("sections", [])
                        if section.get("page") == paragraph.get("page")
                        and section.get("title") == paragraph.get("text")
                    ),
                    None,
                )
            candidate = "\n\n".join([*buffer, paragraph.get("text", "")])
            if buffer and approx_tokens(candidate) > chunk_tokens:
                add("Chunk", start_page, start_position, "\n\n".join(buffer), section_id=section_id)
                buffer = []
            if not buffer:
                start_page, start_position = paragraph.get("page"), paragraph.get("position", 0)
            buffer.append(paragraph.get("text", ""))
        if buffer:
            add("Chunk", start_page, start_position, "\n\n".join(buffer), section_id=section_id)
    else:
        for page in extraction.get("pages", []):
            for chunk in split_text(page.get("markdown") or page.get("text", ""), chunk_tokens, 0):
                add("Chunk", page.get("page"), chunk["ord"], chunk["text"])

    for position, table in enumerate(extraction.get("tables", [])):
        rows = {}
        for cell in sorted(table.get("cells", []), key=lambda item: (item["row"], item["column"])):
            rows.setdefault(cell["row"], []).append(cell.get("text", ""))
        ordered = [" | ".join(row) for _, row in sorted(rows.items())]
        if not ordered:
            continue
        header = ordered[0]
        groups, current = [], [header]
        for row in ordered[1:]:
            if len(current) > 1 and approx_tokens("\n".join([*current, row])) > chunk_tokens:
                groups.append(current)
                current = [header]
            current.append(row)
        groups.append(current)
        for group_index, group in enumerate(groups):
            add(
                "Table",
                table.get("page"),
                f"{position}-{group_index}",
                "\n".join(group),
                table_id=table.get("id"),
                row_group=group_index,
                bounding_regions=table.get("bounding_regions", []),
            )

    for position, figure in enumerate(extraction.get("figures", [])):
        description = "\n".join(
            value for value in [figure.get("caption"), *figure.get("related_text", [])] if value
        )
        add(
            "Figure",
            figure.get("page"),
            position,
            description,
            figure_id=figure.get("id"),
            image_artifact=figure.get("image_artifact"),
            image_status=figure.get("image_status", "unsupported"),
            bounding_regions=figure.get("bounding_regions", []),
        )
    return objects


def _read_int_env(key: str, default: int) -> int:
    value = os.getenv(key)
    if not value:
        return default
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except ValueError:
        return default
