"""Normalization and conservative registry matching."""

from __future__ import annotations
import re
from dataclasses import asdict, dataclass
from rapidfuzz.fuzz import ratio

ALIASES = {
    "ingenuity": "ngenuity.ca",
    "n genuity": "ngenuity.ca",
    "mdw law": "mdwlaw.ca",
    "cody brian": "codybrian.ca",
}


@dataclass(frozen=True)
class Resolution:
    canonical_value: str | None
    match_type: str
    similarity_score: float
    ambiguous: bool
    clarification_required: bool
    candidates: tuple[str, ...] = ()
    requires_confirmation: bool = False

    def dict(self):
        value = asdict(self)
        value["candidates"] = list(self.candidates)
        return value


def normalize_domain(value: str) -> tuple[str, str | None]:
    text = value.casefold().strip().strip(".,!? ")
    text = re.sub(r"\b(?:at|the domain is|domain)\b", " ", text)
    text = re.sub(r"\b(?:dot|period)\b", ".", text)
    text = re.sub(r"[^a-z0-9. ]+", " ", text)
    text = re.sub(r"\s*\.\s*", ".", text)
    bare = re.search(r"\s+([a-z](?:\s*[a-z]){1,})$", text)
    if bare and re.sub(r"\s+", "", bare.group(1)) in {"ca", "com", "org", "net", "me"}:
        text = text[: bare.start()] + "." + re.sub(r"\s+", "", bare.group(1))
    compact = re.sub(r"\s+", "", text).strip(".")
    tld = compact.rsplit(".", 1)[1] if "." in compact else None
    return compact, tld


def match_domain(candidate: str, values: list[str], threshold=90.0, margin=4.0) -> Resolution:
    normalized, explicit_tld = normalize_domain(candidate)
    alias = ALIASES.get(re.sub(r"\s+", " ", candidate.casefold().replace(".", " ")).strip())
    if alias in values:
        return Resolution(alias, "alias", 100.0, False, False)
    exact = [v for v in values if normalized in {v, v.rsplit(".", 1)[0]}]
    if len(exact) > 1:
        return Resolution(None, "ambiguous", 100.0, True, True, tuple(exact))
    if exact:
        return Resolution(
            exact[0], "exact" if normalized == exact[0] else "normalized", 100.0, False, False
        )
    scored = []
    for value in values:
        company, tld = value.rsplit(".", 1)
        score = (
            0.0
            if explicit_tld and explicit_tld != tld
            else float(ratio(normalized, value if "." in normalized else company))
        )
        scored.append((score, value))
    scored.sort(reverse=True)
    best = scored[0]
    second = scored[1] if len(scored) > 1 else (0.0, "")
    if best[0] >= threshold and second[0] >= threshold and best[0] - second[0] < margin:
        return Resolution(None, "ambiguous", best[0], True, True, tuple(x[1] for x in scored[:2]))
    if best[0] >= threshold:
        return Resolution(best[1], "fuzzy", best[0], False, False, requires_confirmation=True)
    # Unknown domains remain usable, but only after explicit confirmation.
    if re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}", normalized):
        return Resolution(normalized, "unknown", best[0], False, False, requires_confirmation=True)
    return Resolution(None, "unresolved", best[0], False, True)
