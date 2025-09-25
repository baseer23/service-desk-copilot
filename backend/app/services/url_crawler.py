"""Utilities for crawling same-origin pages for URL ingestion."""

from __future__ import annotations

import hashlib
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import ParseResult, urljoin, urlparse, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from backend.app.core.config import Settings


@dataclass
class CrawlLimits:
    max_depth: int
    max_pages: int
    max_total_chars: int
    rate_limit_sec: float


@dataclass
class CrawledPage:
    url: str
    title: str
    content: str


@dataclass
class CrawlResult:
    pages: List[CrawledPage] = field(default_factory=list)
    skipped_urls: List[str] = field(default_factory=list)
    pages_visited: int = 0


class CrawlError(Exception):
    """Raised when URL ingestion cannot proceed."""


class UrlCrawler:
    """Same-origin crawler that respects robots, limits, and rate controls."""

    USER_AGENT = "ServiceDeskCopilotBot/1.0 (+https://example.com)"

    def __init__(self, settings: Settings, session: Optional[requests.Session] = None) -> None:
        self._settings = settings
        self._session = session or requests.Session()
        self._session.headers.setdefault("User-Agent", self.USER_AGENT)

    def crawl(self, root_url: str, overrides: Optional[CrawlLimits] = None) -> CrawlResult:
        parsed_root = urlparse(root_url)
        if parsed_root.scheme not in {"http", "https"}:
            raise CrawlError("Only HTTP/HTTPS schemes are supported.")
        if not parsed_root.netloc:
            raise CrawlError("URL must include a hostname.")

        limits = overrides or self._default_limits()
        robot_parser = self._load_robots(parsed_root)

        queue: deque[Tuple[str, int]] = deque([(self._normalize_url(root_url), 0)])
        seen_urls: Set[str] = set()
        seen_hashes: Set[str] = set()
        total_chars = 0
        result = CrawlResult()
        last_fetch = 0.0

        while queue and len(result.pages) < limits.max_pages:
            current_url, depth = queue.popleft()
            if current_url in seen_urls:
                continue
            seen_urls.add(current_url)

            if depth > limits.max_depth:
                result.skipped_urls.append(current_url)
                continue

            if not self._can_fetch(robot_parser, current_url):
                result.skipped_urls.append(current_url)
                continue

            last_fetch = self._respect_rate_limit(last_fetch, limits.rate_limit_sec)
            response = self._safe_get(current_url)
            if response is None:
                result.skipped_urls.append(current_url)
                continue

            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" not in content_type:
                result.skipped_urls.append(current_url)
                continue

            html = response.text
            page_url = self._normalize_url(response.url or current_url)
            parsed_page = urlparse(page_url)
            if parsed_page.scheme != parsed_root.scheme or parsed_page.netloc != parsed_root.netloc:
                result.skipped_urls.append(page_url)
                continue

            soup = BeautifulSoup(html, "html.parser")
            canonical_url = self._canonical_url(soup, page_url)
            if canonical_url != current_url and canonical_url in seen_urls:
                continue

            text, title = self._extract_text_and_title(soup, canonical_url)
            if not text:
                continue

            text_hash = hashlib.sha1(text.encode("utf-8")).hexdigest()
            if text_hash in seen_hashes:
                continue
            seen_hashes.add(text_hash)

            if total_chars + len(text) > limits.max_total_chars:
                break

            result.pages.append(CrawledPage(url=canonical_url, title=title or canonical_url, content=text))
            total_chars += len(text)
            result.pages_visited += 1
            seen_urls.add(canonical_url)

            if depth < limits.max_depth and len(result.pages) < limits.max_pages:
                links = self._extract_same_origin_links(soup, canonical_url, parsed_root)
                for link in links:
                    if link not in seen_urls:
                        queue.append((link, depth + 1))

        return result

    def _default_limits(self) -> CrawlLimits:
        return CrawlLimits(
            max_depth=self._settings.url_max_depth,
            max_pages=max(1, self._settings.url_max_pages),
            max_total_chars=self._settings.url_max_total_chars,
            rate_limit_sec=self._settings.url_rate_limit_sec,
        )

    def _load_robots(self, parsed_root) -> RobotFileParser:
        robots_url = urlunsplit((parsed_root.scheme, parsed_root.netloc, "/robots.txt", "", ""))
        parser = RobotFileParser()
        try:
            response = self._session.get(robots_url, timeout=5)
            if response.status_code >= 400:
                parser.parse([])
            else:
                parser.parse(response.text.splitlines())
        except requests.RequestException:
            parser.parse([])
        parser.modified()
        return parser

    def _can_fetch(self, parser: RobotFileParser, url: str) -> bool:
        try:
            return parser.can_fetch(self.USER_AGENT, url)
        except Exception:  # pragma: no cover - defensive
            return True

    def _respect_rate_limit(self, last_fetch: float, rate_limit_sec: float) -> float:
        if rate_limit_sec <= 0:
            return time.monotonic()
        now = time.monotonic()
        wait = (last_fetch + rate_limit_sec) - now
        if wait > 0:
            time.sleep(wait)
        return time.monotonic()

    def _safe_get(self, url: str) -> Optional[requests.Response]:
        try:
            response = self._session.get(url, timeout=10)
            if response.status_code != 200:
                return None
            return response
        except requests.RequestException:
            return None

    def _normalize_url(self, url: str) -> str:
        parsed = urlsplit(url)
        normalized = urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))
        return normalized.rstrip("/") or normalized

    def _canonical_url(self, soup: BeautifulSoup, fallback_url: str) -> str:
        link = soup.find("link", rel=lambda value: value and "canonical" in value.lower())
        if link and link.get("href"):
            canonical = urljoin(fallback_url, link["href"])
            return self._normalize_url(canonical)
        return self._normalize_url(fallback_url)

    def _extract_text_and_title(self, soup: BeautifulSoup, url: str) -> Tuple[str, str]:
        for tag in soup(["script", "style", "noscript", "template", "nav", "footer", "form", "aside", "iframe"]):
            tag.decompose()

        title_tag = soup.find("title")
        title_text = title_tag.get_text(" ", strip=True) if title_tag else ""

        article = soup.find("article") or soup.find("main") or soup.body or soup
        lines: List[str] = []
        for element in article.descendants:
            if getattr(element, "name", None) in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                heading = element.get_text(" ", strip=True)
                if heading:
                    lines.append(heading)
            elif getattr(element, "name", None) == "p":
                paragraph = element.get_text(" ", strip=True)
                if paragraph:
                    lines.append(paragraph)
        text = "\n\n".join(line for line in lines if line)
        return text.strip(), title_text.strip()

    def _extract_same_origin_links(
        self, soup: BeautifulSoup, base_url: str, root: ParseResult
    ) -> Iterable[str]:
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


def crawl_url(root_url: str, settings: Settings, overrides: Optional[CrawlLimits] = None) -> CrawlResult:
    crawler = UrlCrawler(settings=settings)
    return crawler.crawl(root_url, overrides=overrides)
