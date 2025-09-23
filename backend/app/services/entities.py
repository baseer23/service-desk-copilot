from __future__ import annotations

import re
from typing import Iterable, List

try:  # pragma: no cover - optional dependency
    import spacy
    _NLP = spacy.load("en_core_web_sm")
except Exception:  # pragma: no cover - spaCy not required for offline mode
    _NLP = None


def extract_entities(chunks: Iterable[dict]) -> List[str]:
    texts = [chunk.get("text", "") for chunk in chunks]
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
            # Include trailing segments (e.g. "Widget Alpha" from "Explain Widget Alpha")
            for idx in range(1, len(parts)):
                candidates.append(" ".join(parts[idx:]))
        words = re.findall(r"\b[A-Za-z]{4,}\b", combined)
        candidates.extend(words)

    normalized = {candidate.strip().lower() for candidate in candidates if candidate.strip()}
    return sorted(normalized)
