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
from dataclasses import asdict, dataclass, field
from typing import Iterable, List, Optional, Set, Tuple
from urllib.parse import ParseResult, urljoin, urlparse, urlunsplit
from urllib.robotparser import RobotFileParser

import defusedxml.ElementTree as DefusedET
import requests
from bs4 import BeautifulSoup

from backend.app.core.config import Settings

MAX_SITEMAP_FETCHES = 5
MAX_SITEMAP_DEPTH = 2


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
    html: str = ""
    links: List[str] = field(default_factory=list)


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

        state = self.start(root_url, overrides)
        result = CrawlResult()
        while not state["done"]:
            result.pages.extend(self.advance(state))
        result.skipped = list(state["skipped"])
        result.skipped_urls = [item["url"] for item in state["skipped"]]
        result.pages_visited = len(result.pages)
        if not result.pages:
            raise self.empty_error(state)
        return result

    def start(self, root_url: str, overrides: Optional[CrawlLimits] = None) -> dict:
        """Validate the root, load robots and sitemaps, and return resumable state.

        The state is plain JSON so a crawl job can persist it between steps.
        """

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
        robots_lines = self._load_robots_lines(parsed_root)
        root = self._normalize_url(root_url)
        state = {
            "root_url": root,
            "origin": [parsed_root.scheme, parsed_root.netloc],
            "limits": asdict(limits),
            "robots_lines": robots_lines,
            "queue": [[root, 0]],
            "seen_urls": [],
            "seen_hashes": [],
            "total_chars": 0,
            "pages_accepted": 0,
            "skipped": [],
            "robots_blocked": False,
            "retrieved_html": False,
            "last_fetch_at": 0.0,
            "sitemap_seeds": 0,
            "done": False,
        }
        if limits.max_depth >= 1:
            seeds = self._sitemap_seeds(state)
            state["queue"].extend([url, 1] for url in seeds)
            state["sitemap_seeds"] = len(seeds)
        return state

    def advance(
        self, state: dict, max_pages: int | None = None, deadline: float | None = None
    ) -> List[CrawledPage]:
        """Crawl at most ``max_pages`` pages (or until ``deadline``) from saved state."""

        limits = CrawlLimits(**state["limits"])
        robot_parser = self._parse_robots(state["robots_lines"])
        parsed_root = urlparse(urlunsplit((*state["origin"], "/", "", "")))
        queue = deque(tuple(item) for item in state["queue"])
        seen_urls = set(state["seen_urls"])
        seen_hashes = set(state["seen_hashes"])
        pages: List[CrawledPage] = []

        def skip(url, reason):
            state["skipped"].append({"url": url, "reason": reason})

        while queue and state["pages_accepted"] < limits.max_pages:
            if max_pages is not None and len(pages) >= max_pages:
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            current_url, depth = queue.popleft()
            if current_url in seen_urls:
                continue
            seen_urls.add(current_url)

            if depth > limits.max_depth:
                skip(current_url, "depth_limit")
                continue

            if not self._can_fetch(robot_parser, current_url):
                state["robots_blocked"] = state["robots_blocked"] or depth == 0
                skip(current_url, "robots_disallowed")
                continue

            state["last_fetch_at"] = self._respect_wall_rate_limit(
                state["last_fetch_at"], limits.rate_limit_sec
            )
            response, failure = self._fetch(current_url)
            if response is None and depth == 0 and current_url == state["root_url"]:
                alias = self._www_alias_url(current_url)
                if alias:
                    response, failure = self._fetch(alias)
            if response is None:
                skip(current_url, failure)
                continue

            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" not in content_type:
                skip(current_url, "not_html")
                continue
            state["retrieved_html"] = True

            html = response.text
            page_url = self._normalize_url(response.url or current_url)
            parsed_page = urlparse(page_url)
            if not state["pages_accepted"] and self._is_www_redirect_alias(parsed_root, parsed_page):
                # fetch_response validated every redirect target before requesting it.
                # Adopt only the common apex <-> www canonical transition as the
                # crawl origin; all subsequent traversal remains same-origin.
                parsed_root = parsed_page
                state["origin"] = [parsed_page.scheme, parsed_page.netloc]
            elif parsed_page.scheme != parsed_root.scheme or parsed_page.netloc != parsed_root.netloc:
                skip(page_url, "off_origin")
                continue

            soup = BeautifulSoup(html, "html.parser")
            canonical_url = self._canonical_url(soup, page_url)
            if canonical_url != current_url and canonical_url in seen_urls:
                continue

            text, title = self._extract_text_and_title(soup, canonical_url)
            if not text:
                skip(page_url, "empty")
                continue
            if len(text) > limits.max_page_chars:
                skip(page_url, "too_large")
                continue

            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if text_hash in seen_hashes:
                skip(page_url, "duplicate_content")
                continue

            if state["total_chars"] + len(text) > limits.max_total_chars:
                skip(page_url, "overall_limit")
                queue.clear()
                break
            seen_hashes.add(text_hash)

            links = sorted(self._extract_same_origin_links(soup, canonical_url, parsed_root))
            pages.append(
                CrawledPage(
                    url=page_url,
                    canonical_url=canonical_url,
                    title=title or canonical_url,
                    content=text,
                    html=html,
                    links=[link for link in links if link != canonical_url],
                )
            )
            state["total_chars"] += len(text)
            state["pages_accepted"] += 1
            seen_urls.add(canonical_url)

            if depth < limits.max_depth and state["pages_accepted"] < limits.max_pages:
                for link in links:
                    if link not in seen_urls:
                        queue.append((link, depth + 1))

        state["queue"] = [list(item) for item in queue]
        state["seen_urls"] = sorted(seen_urls)
        state["seen_hashes"] = sorted(seen_hashes)
        state["done"] = not queue or state["pages_accepted"] >= limits.max_pages
        return pages

    @staticmethod
    def empty_error(state: dict) -> "CrawlError":
        """The error a crawl that saved no page reports."""

        if state["robots_blocked"]:
            return CrawlError(
                "ROBOTS_DISALLOWED", "The website does not allow this page to be indexed.", 403
            )
        if state["retrieved_html"]:
            return CrawlError("CRAWL_EMPTY", "The website returned no readable page content.")
        return CrawlError(
            "WEBSITE_UNAVAILABLE",
            "The website could not be retrieved. Check its address or availability.",
            502,
        )

    def _sitemap_seeds(self, state: dict) -> List[str]:
        """Same-origin, robots-allowed URLs from robots "Sitemap:" lines and /sitemap.xml."""

        limits = CrawlLimits(**state["limits"])
        robot_parser = self._parse_robots(state["robots_lines"])
        scheme, netloc = state["origin"]
        pending = list(robot_parser.site_maps() or [])
        default = urlunsplit((scheme, netloc, "/sitemap.xml", "", ""))
        if default not in pending:
            pending.append(default)
        pending = [(url, 0) for url in pending]
        fetched, seeds, visited = 0, [], set()
        while pending and fetched < MAX_SITEMAP_FETCHES and len(seeds) < limits.max_pages:
            sitemap_url, level = pending.pop(0)
            parsed = urlparse(sitemap_url)
            if (parsed.scheme, parsed.netloc) != (scheme, netloc) or sitemap_url in visited:
                continue
            visited.add(sitemap_url)
            state["last_fetch_at"] = self._respect_wall_rate_limit(
                state["last_fetch_at"], limits.rate_limit_sec
            )
            fetched += 1
            response, _ = self._fetch(sitemap_url)
            if response is None:
                continue
            try:
                root = DefusedET.fromstring(response.content)
            except Exception:  # malformed, gzip or forbidden XML constructs
                continue
            kind = root.tag.rsplit("}", 1)[-1]
            for element in root:
                location = next(
                    (child.text for child in element if child.tag.rsplit("}", 1)[-1] == "loc"),
                    None,
                )
                if not location:
                    continue
                location = location.strip()
                if kind == "sitemapindex":
                    if level < MAX_SITEMAP_DEPTH:
                        pending.append((location, level + 1))
                    continue
                url = self._normalize_url(location)
                parsed_url = urlparse(url)
                if (
                    (parsed_url.scheme, parsed_url.netloc) == (scheme, netloc)
                    and url != state["root_url"]
                    and url not in seeds
                    and self._can_fetch(robot_parser, url)
                ):
                    seeds.append(url)
                    if len(seeds) >= limits.max_pages:
                        break
        return seeds

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

        return self._parse_robots(self._load_robots_lines(parsed_root))

    @staticmethod
    def _parse_robots(lines: List[str]) -> RobotFileParser:
        parser = RobotFileParser()
        parser.parse(lines)
        parser.modified()
        return parser

    def _load_robots_lines(self, parsed_root: ParseResult) -> List[str]:
        """Fetch robots.txt lines for the origin; an unavailable file allows all."""

        robots_url = urlunsplit((parsed_root.scheme, parsed_root.netloc, "/robots.txt", "", ""))
        lines: List[str] = []
        # Keep robots-response cookies isolated. Some CDNs issue a host-scoped
        # bot-management cookie for robots.txt that causes a subsequent HTML
        # request in the same session to be rejected even though both requests
        # are independently public and allowed.
        robots_session = requests.Session()
        robots_session.headers.update(self._session.headers)
        try:
            response = fetch_response(robots_url, robots_session, self._settings.web_max_bytes)
            if response.status_code < 400:
                lines = response.text.splitlines()
        except Exception:
            lines = []
        finally:
            close = getattr(robots_session, "close", None)
            if callable(close):
                close()
        return lines

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

    @staticmethod
    def _respect_wall_rate_limit(last_fetch_at: float, rate_limit_sec: float) -> float:
        """Rate-limit with wall-clock time so the interval survives saved state."""

        if rate_limit_sec > 0:
            wait = (last_fetch_at + rate_limit_sec) - time.time()
            if wait > 0:
                time.sleep(wait)
        return time.time()

    def _safe_get(self, url: str) -> Optional[requests.Response]:
        """Perform a GET request handling exceptions and non-200 codes."""

        return self._fetch(url)[0]

    def _fetch(self, url: str) -> Tuple[Optional[requests.Response], str]:
        """GET a page; on failure return (None, reason) without raising."""

        try:
            response = fetch_response(url, self._session, self._settings.web_max_bytes)
        except Exception as exc:
            # fetch_response raises for HTTP errors, so the status lives on the error.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status is not None:
                response = exc.response
            elif "size limit" in str(exc).lower():
                return None, "too_large"
            else:
                return None, "fetch_failed"
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
