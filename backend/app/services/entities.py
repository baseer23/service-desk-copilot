"""Entity extraction utilities with optional spaCy acceleration."""

from __future__ import annotations

import re
from typing import Iterable, List, Mapping

try:  # pragma: no cover - optional dependency
    import spacy

    _NLP = spacy.load("en_core_web_sm")
except Exception:  # pragma: no cover - spaCy not required for offline mode
    _NLP = None


def extract_entities(chunks: Iterable[Mapping[str, object]]) -> List[str]:
    """Return normalized entity candidates extracted from chunk text."""

    texts = [str(chunk.get("text", "")) for chunk in chunks]
    combined = "\n".join(texts)
    candidates: List[str] = []

    if _NLP:  # pragma: no cover - dependent on optional model
        doc = _NLP(combined)
        candidates.extend(ent.text for ent in doc.ents)
        candidates.extend(chunk.text for chunk in doc.noun_chunks)
    else:
        pattern = re.compile(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)")
        for phrase in pattern.findall(combined):
            candidates.append(phrase)
            parts = phrase.split()
            for idx in range(1, len(parts)):
                candidates.append(" ".join(parts[idx:]))
        words = re.findall(r"\b[A-Za-z]{4,}\b", combined)
        candidates.extend(words)

    normalized = {candidate.strip().lower() for candidate in candidates if candidate.strip()}
    return sorted(normalized)
