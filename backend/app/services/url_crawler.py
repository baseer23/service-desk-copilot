"""Utilities for crawling same-origin pages for URL ingestion."""

from __future__ import annotations

import hashlib
from backend.app.services.web import (
    fetch_response,
    validate_public_url,
    extract_html,
    normalize_url,
)
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Iterable, List, Optional, Set, Tuple
from urllib.parse import ParseResult, urljoin, urlparse, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from backend.app.core.config import Settings


@dataclass
class CrawlLimits:
    """Bounds used to control crawl scope and rate."""

    max_depth: int
    max_pages: int
    max_total_chars: int
    rate_limit_sec: float
    max_page_chars: int = 200_000


@dataclass
class CrawledPage:
    """Represents content extracted from a crawled page."""

    url: str
    title: str
    content: str
    canonical_url: str = ""


@dataclass
class CrawlResult:
    """Aggregate crawl outcome including visited pages and skips."""

    pages: List[CrawledPage] = field(default_factory=list)
    skipped_urls: List[str] = field(default_factory=list)
    skipped: List[dict] = field(default_factory=list)
    pages_visited: int = 0

    def skip(self, url: str, reason: str) -> None:
        """Record a skipped URL with a user-facing reason code."""
        self.skipped_urls.append(url)
        self.skipped.append({"url": url, "reason": reason})


class CrawlError(ValueError):
    """Raised when URL ingestion cannot proceed."""

    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class UrlCrawler:
    """Same-origin crawler that respects robots, limits, and rate controls."""

    USER_AGENT = "ServiceDeskCopilotBot/1.0 (+https://example.com)"

    def __init__(self, settings: Settings, session: Optional[requests.Session] = None) -> None:
        """Store configuration and instantiate an HTTP session if needed."""
        self._settings = settings
        self._session = session or requests.Session()
        self._session.headers.setdefault("User-Agent", self.USER_AGENT)

    def crawl(self, root_url: str, overrides: Optional[CrawlLimits] = None) -> CrawlResult:
        """Crawl the supplied root URL and return extracted content."""

        try:
            validate_public_url(root_url)
        except ValueError as exc:
            raise CrawlError("URL_BLOCKED", str(exc), 400) from exc
        parsed_root = urlparse(root_url)
        if parsed_root.scheme not in {"http", "https"}:
            raise CrawlError("URL_BLOCKED", "Only HTTP/HTTPS schemes are supported.", 400)
        if not parsed_root.netloc:
            raise CrawlError("URL_BLOCKED", "URL must include a hostname.", 400)

        limits = overrides or self._default_limits()
        robot_parser = self._load_robots(parsed_root)

        queue: Deque[Tuple[str, int]] = deque([(self._normalize_url(root_url), 0)])
        seen_urls: Set[str] = set()
        seen_hashes: Set[str] = set()
        total_chars = 0
        result = CrawlResult()
        robots_blocked = False
        retrieved_html = False
        last_fetch = 0.0

        while queue and len(result.pages) < limits.max_pages:
            current_url, depth = queue.popleft()
            if current_url in seen_urls:
                continue
            seen_urls.add(current_url)

            if depth > limits.max_depth:
                result.skip(current_url, "depth_limit")
                continue

            if not self._can_fetch(robot_parser, current_url):
                robots_blocked = robots_blocked or depth == 0
                result.skip(current_url, "robots_disallowed")
                continue

            last_fetch = self._respect_rate_limit(last_fetch, limits.rate_limit_sec)
            response, failure = self._fetch(current_url)
            if response is None and depth == 0 and current_url == self._normalize_url(root_url):
                alias = self._www_alias_url(current_url)
                if alias:
                    response, failure = self._fetch(alias)
            if response is None:
                result.skip(current_url, failure)
                continue

            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" not in content_type:
                result.skip(current_url, "not_html")
                continue
            retrieved_html = True

            html = response.text
            page_url = self._normalize_url(response.url or current_url)
            parsed_page = urlparse(page_url)
            if not result.pages and self._is_www_redirect_alias(parsed_root, parsed_page):
                # fetch_response validated every redirect target before requesting it.
                # Adopt only the common apex <-> www canonical transition as the
                # crawl origin; all subsequent traversal remains same-origin.
                parsed_root = parsed_page
            elif parsed_page.scheme != parsed_root.scheme or parsed_page.netloc != parsed_root.netloc:
                result.skip(page_url, "off_origin")
                continue

            soup = BeautifulSoup(html, "html.parser")
            canonical_url = self._canonical_url(soup, page_url)
            if canonical_url != current_url and canonical_url in seen_urls:
                continue

            text, title = self._extract_text_and_title(soup, canonical_url)
            if not text:
                result.skip(page_url, "empty")
                continue
            if len(text) > limits.max_page_chars:
                result.skip(page_url, "too_large")
                continue

            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if text_hash in seen_hashes:
                result.skip(page_url, "duplicate_content")
                continue
            seen_hashes.add(text_hash)

            if total_chars + len(text) > limits.max_total_chars:
                result.skip(page_url, "overall_limit")
                break

            result.pages.append(
                CrawledPage(
                    url=page_url,
                    canonical_url=canonical_url,
                    title=title or canonical_url,
                    content=text,
                )
            )
            total_chars += len(text)
            result.pages_visited += 1
            seen_urls.add(canonical_url)

            if depth < limits.max_depth and len(result.pages) < limits.max_pages:
                links = self._extract_same_origin_links(soup, canonical_url, parsed_root)
                for link in links:
                    if link not in seen_urls:
                        queue.append((link, depth + 1))

        if not result.pages:
            if robots_blocked:
                raise CrawlError(
                    "ROBOTS_DISALLOWED", "The website does not allow this page to be indexed.", 403
                )
            if retrieved_html:
                raise CrawlError("CRAWL_EMPTY", "The website returned no readable page content.")
            raise CrawlError(
                "WEBSITE_UNAVAILABLE",
                "The website could not be retrieved. Check its address or availability.",
                502,
            )
        return result

    def _default_limits(self) -> CrawlLimits:
        """Construct CrawlLimits using application settings."""

        return CrawlLimits(
            max_depth=self._settings.url_max_depth,
            max_pages=max(1, self._settings.url_max_pages),
            max_total_chars=self._settings.url_max_total_chars,
            rate_limit_sec=self._settings.url_rate_limit_sec,
            max_page_chars=self._settings.url_max_page_chars,
        )

    def _load_robots(self, parsed_root: ParseResult) -> RobotFileParser:
        """Load and parse robots.txt for the target origin."""

        robots_url = urlunsplit((parsed_root.scheme, parsed_root.netloc, "/robots.txt", "", ""))
        parser = RobotFileParser()
        # Keep robots-response cookies isolated. Some CDNs issue a host-scoped
        # bot-management cookie for robots.txt that causes a subsequent HTML
        # request in the same session to be rejected even though both requests
        # are independently public and allowed.
        robots_session = requests.Session()
        robots_session.headers.update(self._session.headers)
        try:
            response = fetch_response(robots_url, robots_session, self._settings.web_max_bytes)
            if response.status_code >= 400:
                parser.parse([])
            else:
                parser.parse(response.text.splitlines())
        except Exception:
            parser.parse([])
        finally:
            close = getattr(robots_session, "close", None)
            if callable(close):
                close()
        parser.modified()
        return parser

    def _can_fetch(self, parser: RobotFileParser, url: str) -> bool:
        """Return True when the crawler is permitted to fetch the given URL."""

        try:
            return parser.can_fetch(self.USER_AGENT, url)
        except Exception:  # pragma: no cover - defensive
            return True

    def _respect_rate_limit(self, last_fetch: float, rate_limit_sec: float) -> float:
        """Sleep as required to honour crawl rate limits and return new timestamp."""

        if rate_limit_sec <= 0:
            return time.monotonic()
        now = time.monotonic()
        wait = (last_fetch + rate_limit_sec) - now
        if wait > 0:
            time.sleep(wait)
        return time.monotonic()

    def _safe_get(self, url: str) -> Optional[requests.Response]:
        """Perform a GET request handling exceptions and non-200 codes."""

        return self._fetch(url)[0]

    def _fetch(self, url: str) -> Tuple[Optional[requests.Response], str]:
        """GET a page; on failure return (None, reason) without raising."""

        try:
            response = fetch_response(url, self._session, self._settings.web_max_bytes)
        except Exception as exc:
            reason = "too_large" if "size limit" in str(exc).lower() else "fetch_failed"
            return None, reason
        if response.status_code == 429:
            return None, "rate_limited"
        if response.status_code != 200:
            return None, f"http_{response.status_code}"
        return response, ""

    def _normalize_url(self, url: str) -> str:
        """Return a normalised URL suitable for deduplication."""

        return normalize_url(url)

    @staticmethod
    def _www_alias_url(url: str) -> str | None:
        parsed = urlparse(url)
        if not parsed.hostname or parsed.hostname.startswith("www."):
            return None
        hostname = "www." + parsed.hostname
        netloc = hostname + (f":{parsed.port}" if parsed.port else "")
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))

    @staticmethod
    def _is_www_redirect_alias(root: ParseResult, destination: ParseResult) -> bool:
        def host(value: ParseResult) -> str:
            return (value.hostname or "").lower().removeprefix("www.")

        def port(value: ParseResult) -> int:
            return value.port or (443 if value.scheme == "https" else 80)

        return (
            root.scheme == destination.scheme
            and port(root) == port(destination)
            and host(root) == host(destination)
            and root.hostname != destination.hostname
            and "www." in {(root.hostname or "")[:4].lower(), (destination.hostname or "")[:4].lower()}
        )

    def _canonical_url(self, soup: BeautifulSoup, fallback_url: str) -> str:
        """Resolve a canonical URL from the page when provided."""

        page, _ = extract_html(str(soup), fallback_url)
        return page["url"]

    def _extract_text_and_title(self, soup, url):
        page, _ = extract_html(str(soup), url)
        return page["text"], page["title"]

    def _extract_same_origin_links(
        self, soup: BeautifulSoup, base_url: str, root: ParseResult
    ) -> Iterable[str]:
        """Yield same-origin links discovered within the provided soup."""

        parsed_root = root
        collected: Set[str] = set()
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href", "").strip()
            if not href:
                continue
            joined = urljoin(base_url, href)
            normalized = self._normalize_url(joined)
            parsed = urlparse(normalized)
            if parsed.scheme != parsed_root.scheme or parsed.netloc != parsed_root.netloc:
                continue
            collected.add(normalized)
        return collected


def crawl_url(
    root_url: str, settings: Settings, overrides: Optional[CrawlLimits] = None
) -> CrawlResult:
    """Convenience helper constructing a UrlCrawler and invoking crawl."""

    crawler = UrlCrawler(settings=settings)
    return crawler.crawl(root_url, overrides=overrides)
