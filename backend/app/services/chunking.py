from __future__ import annotations

import os
import re
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


def split_text(text: str, chunk_tokens: int | None = None, overlap: int | None = None) -> List[Dict[str, object]]:
    """Split raw text into chunk dictionaries with token metadata."""
    tokens = re.findall(r"\S+", text)
    if not tokens:
        return []

    chunk_size = chunk_tokens or _read_int_env("CHUNK_TOKENS", DEFAULT_CHUNK_TOKENS)
    chunk_overlap = overlap or _read_int_env("CHUNK_OVERLAP", DEFAULT_CHUNK_OVERLAP)

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


def _read_int_env(key: str, default: int) -> int:
    value = os.getenv(key)
    if not value:
        return default
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except ValueError:
        return default
