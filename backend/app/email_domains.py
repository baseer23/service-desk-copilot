"""Validated, file-backed approved email-domain registry."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock

from rapidfuzz.fuzz import ratio

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "approved_email_domains.txt"
DOMAIN_RE = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")


def normalize_spoken(value: str) -> tuple[str, str | None]:
    text = value.casefold().strip()
    text = re.sub(r"\b(?:at|the domain is|domain)\b", " ", text)
    text = re.sub(r"\b(?:dot|period)\b", ".", text)
    text = re.sub(r"[^a-z0-9. ]+", " ", text)
    text = re.sub(r"\s*\.\s*", ".", text)
    explicit_tld = None
    bare_tld = re.search(r"\s+([a-z](?:\s*[a-z]){1,})\s*$", text)
    if bare_tld and re.sub(r"\s+", "", bare_tld.group(1)) in {"ca", "com", "org", "net"}:
        explicit_tld = re.sub(r"\s+", "", bare_tld.group(1))
        text = text[: bare_tld.start()] + "." + explicit_tld
    tld = re.search(r"\.\s*([a-z](?:\s+[a-z]){1,})\s*$", text)
    if tld:
        explicit_tld = re.sub(r"\s+", "", tld.group(1))
        text = text[: tld.start()] + "." + explicit_tld
    compact = re.sub(r"\s+", "", text)
    return compact.strip("."), explicit_tld


@dataclass(frozen=True)
class DomainResolution:
    canonical_domain: str | None
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


class ApprovedDomainRegistry:
    def __init__(self, path: Path = DEFAULT_PATH, threshold: float = 90.0, margin: float = 4.0):
        self.path, self.threshold, self.margin = path, threshold, margin
        self._lock = RLock()
        self._domains: tuple[str, ...] = ()
        self.reload()

    def reload(self) -> list[str]:
        values = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            value = line.split("#", 1)[0].strip().casefold()
            if not value:
                continue
            if not DOMAIN_RE.fullmatch(value):
                raise ValueError(f"Invalid approved email domain: {value}")
            if value in values:
                raise ValueError(f"Duplicate approved email domain: {value}")
            values.append(value)
        if not values:
            raise ValueError("Approved email-domain file is empty")
        with self._lock:
            self._domains = tuple(values)
        return values

    def list(self) -> list[str]:
        with self._lock:
            return list(self._domains)

    @staticmethod
    def forms(domain: str) -> set[str]:
        company, tld = domain.rsplit(".", 1)
        letters = "".join(company.split())
        return {domain, company, letters, f"{letters}.{tld}"}

    def resolve(self, spoken: str, current_domain: str | None = None) -> DomainResolution:
        normalized, explicit_tld = normalize_spoken(spoken)
        domains = self.list()
        exact = [domain for domain in domains if normalized in self.forms(domain)]
        if len(exact) > 1:
            return DomainResolution(None, "ambiguous", 100.0, True, True, tuple(exact))
        if exact:
            kind = "exact" if normalized == exact[0] else "normalized"
            return DomainResolution(exact[0], kind, 100.0, False, False)
        scores = []
        for domain in domains:
            company, tld = domain.rsplit(".", 1)
            if explicit_tld and explicit_tld != tld:
                score = 0.0
            else:
                target = domain if "." in normalized else company
                score = float(ratio(normalized, target))
            scores.append((score, domain))
        scores.sort(reverse=True)
        best, second = scores[0], scores[1] if len(scores) > 1 else (0.0, "")
        close = (
            best[0] >= self.threshold
            and second[0] >= self.threshold
            and best[0] - second[0] < self.margin
        )
        if close:
            return DomainResolution(
                None, "ambiguous", best[0], True, True, tuple(x[1] for x in scores[:2])
            )
        if best[0] >= self.threshold:
            return DomainResolution(
                best[1], "fuzzy", best[0], False, False, requires_confirmation=True
            )
        return DomainResolution(current_domain, "unresolved", best[0], False, True)


registry = ApprovedDomainRegistry()
