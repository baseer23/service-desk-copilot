"""Deterministic checks that a model's proposal is grounded in cited evidence."""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from backend.app.services.form_marks import readable_marks

FUZZY_MENTION = 92
# Claim statuses, weakest last. A validator may lower a status, never raise it.
VERIFIED_STATUSES = ("observed", "derived")
CLAIM_STATUSES = ("observed", "derived", "ambiguous", "hypothesis")


def normalize(text: str) -> str:
    text = readable_marks(str(text or "")).casefold()
    text = re.sub(r"[^\w\s$%./-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def mentions(text: str, names) -> bool:
    """Whether text names one of ``names`` (whole words, or a near-exact long name)."""
    haystack = normalize(text)
    for name in names:
        needle = normalize(name)
        if not needle:
            continue
        if re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack):
            return True
        if len(needle) >= 6 and fuzz.partial_ratio(needle, haystack) >= FUZZY_MENTION:
            return True
    return False


def weaker(status: str, other: str) -> str:
    """The weaker of two claim statuses."""
    return max(status, other, key=CLAIM_STATUSES.index)
