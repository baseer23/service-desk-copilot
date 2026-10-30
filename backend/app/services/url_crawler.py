"""Utilities for crawling same-origin pages for URL ingestion."""

from __future__ import annotations

import hashlib
from backend.app.services.web import (
    fetch_response,
    is_www_alias,
    same_site_url,
    validate_public_url,
    extract_html,
    normalize_url,
)
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from email.utils import parsedate_to_datetime
from typing import Iterable, List, Optional, Set, Tuple
from urllib.parse import ParseResult, urljoin, urlparse, urlunsplit
from urllib.robotparser import RobotFileParser

import defusedxml.ElementTree as DefusedET
import requests
from bs4 import BeautifulSoup

from backend.app.core.config import Settings

MAX_SITEMAP_FETCHES = 5
MAX_SITEMAP_DEPTH = 2
USER_AGENT_PRODUCT = "DeskMateBot/1.0"
ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"
ACCEPT_LANGUAGE = "en-CA,en;q=0.8"
# Consecutive HTTP 403s after which the site is treated as refusing the crawler.
MAX_CONSECUTIVE_FORBIDDEN = 3
DEFAULT_RETRY_AFTER_SEC = 60
MAX_RETRY_AFTER_SEC = 900
MAX_RATE_LIMIT_RETRIES = 3
# Cookies that bot-protection services use to track or clear automated
# clients. They are never kept or replayed: DeskMate does not bypass them.
BOT_PROTECTION_COOKIES = (
    "cf_clearance", "__cf_bm", "_cfuvid", "__cfruid", "datadome", "_abck", "bm_sz",
    "ak_bmsc", "incap_ses_", "visid_incap_", "reese84",
)
CHALLENGE_MARKERS = (
    "/cdn-cgi/challenge-platform/", "<title>just a moment...</title>",
    "captcha-delivery.com", "_incapsula_resource",
)


def crawler_user_agent(settings) -> str:
    """An honest crawler identity with the operator's contact URL when configured."""
    contact = str(getattr(settings, "url_crawler_contact", "") or "").strip()
    return f"{USER_AGENT_PRODUCT} (+{contact})" if contact else USER_AGENT_PRODUCT


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

    USER_AGENT = USER_AGENT_PRODUCT

    def __init__(self, settings: Settings, session: Optional[requests.Session] = None) -> None:
        """Store configuration and instantiate an HTTP session if needed."""
        self._settings = settings
        self.user_agent = crawler_user_agent(settings)
        # One session per crawl step; requests' own default User-Agent must
        # not survive, so the identity is set rather than defaulted.
        self._session = session or requests.Session()
        self._session.headers.update(
            {"User-Agent": self.user_agent, "Accept": ACCEPT, "Accept-Language": ACCEPT_LANGUAGE}
        )
        self._prefetched: dict = {}
        self.last_retry_after = DEFAULT_RETRY_AFTER_SEC

    def crawl(self, root_url: str, overrides: Optional[CrawlLimits] = None) -> CrawlResult:
        """Crawl the supplied root URL and return extracted content."""

        state = self.start(root_url, overrides)
        result = CrawlResult()
        while not state["done"]:
            wait = state.get("resume_after", 0) - time.time()
            if wait > 0:
                time.sleep(wait)
            result.pages.extend(self.advance(state))
        result.skipped = list(state["skipped"])
        result.skipped_urls = [item["url"] for item in state["skipped"]]
        result.pages_visited = len(result.pages)
        if not result.pages:
            raise self.empty_error(state)
        return result

    def start(self, root_url: str, overrides: Optional[CrawlLimits] = None) -> dict:
        """Validate the root, resolve its canonical origin, load robots and sitemaps.

        The root is fetched once here (and handed to the first ``advance``) so an
        apex <-> www redirect is adopted before robots.txt and sitemaps are read
        from the canonical origin. The state is plain JSON so a crawl job can
        persist it between steps.
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
        root = self._normalize_url(root_url)
        state = {
            "root_url": root,
            "origin": [parsed_root.scheme, parsed_root.netloc],
            "limits": asdict(limits),
            "robots_lines": self._load_robots_lines(parsed_root),
            "crawl_delay": 0.0,
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
            "consecutive_forbidden": 0,
            "rate_limit_retries": {},
            "resume_after": 0.0,
            "blocked_by_site": None,
            "done": False,
        }
        self._set_crawl_delay(state)
        if self._can_fetch(self._parse_robots(state["robots_lines"]), root):
            self._resolve_canonical_root(state)
        if limits.max_depth >= 1:
            seeds = self._sitemap_seeds(state)
            state["queue"].extend([url, 1] for url in seeds)
            state["sitemap_seeds"] = len(seeds)
        return state

    def _resolve_canonical_root(self, state: dict) -> None:
        """Fetch the root once and adopt an apex <-> www redirect as the crawl origin."""

        root = state["root_url"]
        state["last_fetch_at"] = self._respect_wall_rate_limit(
            state["last_fetch_at"], self._delay(state)
        )
        response, failure = self._fetch(root)
        if response is None and failure not in {"bot_challenge", "rate_limited"}:
            alias = self._www_alias_url(root)
            if alias:
                alias_response, _ = self._fetch(alias)
                if alias_response is not None:
                    response, failure = alias_response, ""
        if response is not None:
            final = urlparse(self._normalize_url(response.url or root))
            if self._is_www_redirect_alias(urlparse(root), final):
                # fetch_response validated every redirect target before requesting it.
                canonical = self._normalize_url(
                    urlunsplit((final.scheme, final.netloc, final.path, final.query, ""))
                )
                state["origin"] = [final.scheme, final.netloc]
                state["root_url"] = canonical
                state["queue"] = [[canonical, 0]]
                robots = self._load_robots_lines(final)
                if robots:
                    state["robots_lines"] = robots
                    self._set_crawl_delay(state)
                root = canonical
        self._prefetched[root] = (response, failure)

    def _set_crawl_delay(self, state: dict) -> None:
        try:
            delay = self._parse_robots(state["robots_lines"]).crawl_delay(self.user_agent)
        except (TypeError, ValueError):  # pragma: no cover - malformed Crawl-delay
            delay = None
        state["crawl_delay"] = float(delay or 0)

    @staticmethod
    def _delay(state: dict) -> float:
        """The configured rate limit, or robots.txt Crawl-delay when that is longer."""
        return max(float(state["limits"]["rate_limit_sec"]), float(state.get("crawl_delay") or 0))

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
        state.setdefault("consecutive_forbidden", 0)
        state.setdefault("rate_limit_retries", {})

        def skip(url, reason):
            state["skipped"].append({"url": url, "reason": reason})

        while (
            queue
            and state["pages_accepted"] < limits.max_pages
            and not state.get("blocked_by_site")
            and state.get("resume_after", 0) <= time.time()
        ):
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

            if current_url in self._prefetched:
                response, failure = self._prefetched.pop(current_url)
            else:
                state["last_fetch_at"] = self._respect_wall_rate_limit(
                    state["last_fetch_at"], self._delay(state)
                )
                response, failure = self._fetch(current_url)
                if response is None and depth == 0 and current_url == state["root_url"]:
                    alias = self._www_alias_url(current_url)
                    if alias and failure not in {"bot_challenge", "rate_limited"}:
                        response, failure = self._fetch(alias)
            if response is None:
                if failure == "rate_limited":
                    retries = state["rate_limit_retries"].get(current_url, 0) + 1
                    state["rate_limit_retries"][current_url] = retries
                    if retries <= MAX_RATE_LIMIT_RETRIES:
                        # Resume this URL after the site's Retry-After instead of skipping it.
                        seen_urls.discard(current_url)
                        queue.appendleft((current_url, depth))
                        state["resume_after"] = time.time() + self.last_retry_after
                        if self.last_retry_after > 0:
                            break
                        continue
                skip(current_url, failure)
                if failure == "bot_challenge":
                    state["blocked_by_site"] = {"reason": "bot_challenge", "url": current_url}
                    break
                if failure == "http_403":
                    state["consecutive_forbidden"] += 1
                    if state["consecutive_forbidden"] >= MAX_CONSECUTIVE_FORBIDDEN:
                        state["blocked_by_site"] = {"reason": "http_403", "url": current_url}
                        break
                continue
            state["consecutive_forbidden"] = 0

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
        state["done"] = (
            bool(state.get("blocked_by_site"))
            or not queue
            or state["pages_accepted"] >= limits.max_pages
        )
        return pages

    @staticmethod
    def empty_error(state: dict) -> "CrawlError":
        """The error a crawl that saved no page reports."""

        if state.get("blocked_by_site"):
            return CrawlError("BLOCKED_BY_SITE", "The website refused automated access.", 403)
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
        """Robots-allowed URLs from robots "Sitemap:" lines and /sitemap.xml.

        Sitemaps and their URLs on the site's apex/www alias are rewritten to
        the canonical origin, so pages are not fetched through redirects.
        """

        limits = CrawlLimits(**state["limits"])
        robot_parser = self._parse_robots(state["robots_lines"])
        scheme, netloc = state["origin"]
        origin = urlunsplit((scheme, netloc, "/", "", ""))
        pending = list(robot_parser.site_maps() or [])
        default = urlunsplit((scheme, netloc, "/sitemap.xml", "", ""))
        if default not in pending:
            pending.append(default)
        pending = [(url, 0) for url in pending]
        fetched, seeds, visited = 0, [], set()
        while pending and fetched < MAX_SITEMAP_FETCHES and len(seeds) < limits.max_pages:
            sitemap_url, level = pending.pop(0)
            sitemap_url = same_site_url(sitemap_url, origin)
            if not sitemap_url or sitemap_url in visited:
                continue
            visited.add(sitemap_url)
            state["last_fetch_at"] = self._respect_wall_rate_limit(
                state["last_fetch_at"], self._delay(state)
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
                url = same_site_url(self._normalize_url(location), origin)
                if (
                    url
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
            return parser.can_fetch(self.user_agent, url)
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
        finally:
            self._drop_bot_protection_cookies()
        if self._is_challenge(response):
            return None, "bot_challenge"
        if response.status_code == 429:
            self.last_retry_after = self._retry_after(response)
            return None, "rate_limited"
        if response.status_code != 200:
            return None, f"http_{response.status_code}"
        return response, ""

    @staticmethod
    def _header(response, name: str) -> str:
        headers = getattr(response, "headers", None) or {}
        return next(
            (str(value) for key, value in headers.items() if key.lower() == name.lower()), ""
        )

    def _is_challenge(self, response) -> bool:
        """A bot-protection challenge page rather than the requested content."""

        if self._header(response, "cf-mitigated").strip().lower() == "challenge":
            return True
        if response.status_code not in {403, 429, 503}:
            return False
        try:
            body = (response.text or "")[:20_000].lower()
        except Exception:  # noqa: BLE001 - a closed error stream has no readable body
            return False
        return any(marker in body for marker in CHALLENGE_MARKERS)

    def _retry_after(self, response) -> float:
        """Seconds the site asked to wait (seconds or HTTP date), bounded."""

        value = self._header(response, "Retry-After").strip()
        seconds = DEFAULT_RETRY_AFTER_SEC
        if value:
            try:
                seconds = float(value)
            except ValueError:
                try:
                    seconds = parsedate_to_datetime(value).timestamp() - time.time()
                except (TypeError, ValueError):
                    seconds = DEFAULT_RETRY_AFTER_SEC
        return min(max(seconds, 0.0), MAX_RETRY_AFTER_SEC)

    def _drop_bot_protection_cookies(self) -> None:
        cookies = getattr(self._session, "cookies", None)
        if cookies is None:
            return
        for cookie in list(cookies):
            if cookie.name.startswith(BOT_PROTECTION_COOKIES):
                cookies.clear(cookie.domain, cookie.path, cookie.name)

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
        return is_www_alias(root, destination)

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
        """Same-site links in the soup; apex/www alias links use the crawl origin."""

        origin = urlunsplit((root.scheme, root.netloc, "/", "", ""))
        collected: Set[str] = set()
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href", "").strip()
            if not href:
                continue
            normalized = same_site_url(self._normalize_url(urljoin(base_url, href)), origin)
            if normalized:
                collected.add(normalized)
        return collected


def crawl_url(
    root_url: str, settings: Settings, overrides: Optional[CrawlLimits] = None
) -> CrawlResult:
    """Convenience helper constructing a UrlCrawler and invoking crawl."""

    crawler = UrlCrawler(settings=settings)
    return crawler.crawl(root_url, overrides=overrides)
