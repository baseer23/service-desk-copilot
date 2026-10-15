"""Configured public-source provenance/ranking; retrieval never grants write permission."""

from typing import Any, Literal
from urllib.parse import urlsplit

from backend.app.core.config import Settings


def is_primary(url: str, settings: Settings) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(
        host == domain or host.endswith("." + domain)
        for domain in settings.research_preferred_domains
    )


def permits(url: str, settings: Settings) -> bool:
    return settings.research_source_policy != "primary_only" or is_primary(url, settings)


def quality(url: str, settings: Settings) -> Literal["primary", "third_party"]:
    return "primary" if is_primary(url, settings) else "third_party"


def rank_sources(rows: list[dict[str, Any]], settings: Settings) -> list[dict[str, Any]]:
    allowed = []
    seen = set()
    for row in rows:
        url = row.get("url", "")
        if not isinstance(url, str) or not permits(url, settings):
            continue
        parsed = urlsplit(url)
        canonical = parsed._replace(
            scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower(), fragment=""
        ).geturl()
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        allowed.append({**row, "source_quality": quality(url, settings)})
    if settings.research_source_policy == "allow_third_party":
        return allowed
    return sorted(allowed, key=lambda row: 0 if row["source_quality"] == "primary" else 1)
