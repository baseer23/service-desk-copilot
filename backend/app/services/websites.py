"""Tenant-scoped website registrations and explicit crawl policy records."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from backend.app.services.web import normalize_url, validate_public_url


def _now() -> str:
    return datetime.now(UTC).isoformat()


class WebsiteRepository:
    """Persist control-plane state without claiming that crawling is active."""

    def __init__(self, records):
        self.records = records

    @staticmethod
    def registration_root(url: str) -> str:
        candidate = url.strip()
        parsed = validate_public_url(candidate)
        port = parsed.port
        default_port = (parsed.scheme == "https" and port == 443) or (
            parsed.scheme == "http" and port == 80
        )
        authority = parsed.hostname.lower()
        if port and not default_port:
            authority += f":{port}"
        normalized = urlsplit(
            normalize_url(urlsplit(candidate)._replace(netloc=authority).geturl())
        )
        return urlunsplit(
            (normalized.scheme, normalized.netloc, normalized.path or "/", normalized.query, "")
        )

    def register(
        self,
        url: str,
        *,
        max_depth: int = 1,
        max_pages: int = 20,
        request_budget: int = 40,
        allow_subdomains: bool = False,
    ) -> dict:
        root = self.registration_root(url)
        if not 0 <= max_depth <= 3:
            raise ValueError("Website depth must be between 0 and 3")
        if not 1 <= max_pages <= 1000:
            raise ValueError("Website page limit must be between 1 and 1000")
        if not max_pages <= request_budget <= 2000:
            raise ValueError("Request budget must cover the page limit and be at most 2000")
        website_id = hashlib.sha256(f"{self.records.tenant()}\n{root}".encode()).hexdigest()
        previous = self.records.read("website", website_id)
        if previous:
            return {**previous["payload"], "duplicate": True}
        created = _now()
        payload = {
            "id": website_id,
            "root_url": root,
            "origin": f"{urlsplit(root).scheme}://{urlsplit(root).netloc}",
            "allowed_hosts": [urlsplit(root).hostname],
            "allow_subdomains": allow_subdomains,
            "max_depth": max_depth,
            "max_pages": max_pages,
            "request_budget": request_budget,
            "status": "registered",
            "monitoring_active": False,
            "crawl_status": "not_started",
            "graph_link_status": "not_started",
            "created_at": created,
            "updated_at": created,
        }
        self.records.write(
            "website", website_id, payload, create_only=True, fields={"status": "registered"}
        )
        return {**payload, "duplicate": False}

    def list(self) -> list[dict]:
        return [row["payload"] for row in self.records.query("website", limit=1000)]

    def get(self, website_id: str) -> dict:
        row = self.records.read("website", website_id)
        if not row:
            raise ValueError("Website registration not found")
        return row["payload"]

    def set_paused(self, website_id: str, paused: bool) -> dict:
        row = self.records.read("website", website_id)
        if not row:
            raise ValueError("Website registration not found")
        state = dict(row["payload"])
        state.update(
            status="paused" if paused else "registered",
            monitoring_active=False,
            updated_at=_now(),
        )
        self.records.write(
            "website", website_id, state, previous=row, fields={"status": state["status"]}
        )
        return state
