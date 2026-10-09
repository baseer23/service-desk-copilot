"""Shared public URL validation, bounded fetching and readable HTML extraction."""

import ipaddress
import socket
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit, urlunsplit
import requests
from bs4 import BeautifulSoup


class UnsafeURL(ValueError):
    pass


class FetchError(RuntimeError):
    pass


def validate_public_url(url):
    try:
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeURL("Invalid URL") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise UnsafeURL("Only public HTTP(S) URLs without credentials are allowed")
    host = parsed.hostname.rstrip(".").lower()
    if (
        host == "localhost"
        or host.endswith((".localhost", ".local", ".internal"))
        or host == "metadata.google.internal"
    ):
        raise UnsafeURL("Local and metadata hosts are forbidden")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise UnsafeURL("URL hostname could not be resolved") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise UnsafeURL("Private, loopback, link-local and reserved destinations are forbidden")
    return parsed


def fetch_response(url, session=None, max_bytes=2_000_000):
    session = session or requests.Session()
    session.trust_env = False
    for _ in range(6):
        validate_public_url(url)
        response = session.get(url, timeout=(5, 15), allow_redirects=False, stream=True)
        if response.status_code in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise FetchError("Redirect lacks a destination")
            url = urljoin(url, location)
            # Validate BEFORE any request to the redirect target.
            validate_public_url(url)
            continue
        try:
            response.raise_for_status()
            if int(response.headers.get("Content-Length", "0")) > max_bytes:
                raise FetchError("Web response exceeds the size limit")
            data = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise FetchError("Web response exceeds the size limit")
            response._content = bytes(data)
            response._content_consumed = True
            return response
        finally:
            response.close()
    raise FetchError("Too many redirects")


def normalize_url(url):
    p = urlsplit(url)
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.query, "")).rstrip("/")


def extract_html(html, url):
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else url
    for tag in soup(
        ["script", "style", "noscript", "template", "nav", "footer", "form", "aside", "iframe"]
    ):
        tag.decompose()
    article = soup.find("article") or soup.find("main") or soup.body or soup
    blocks = article.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre"])
    text = (
        "\n\n".join(b.get_text(" ", strip=True) for b in blocks)
        if blocks
        else article.get_text(" ", strip=True)
    )
    canonical = soup.find("link", rel="canonical")
    canonical_url = urljoin(url, canonical.get("href", "")) if canonical else url
    if (
        urlsplit(canonical_url).netloc != urlsplit(url).netloc
        or urlsplit(canonical_url).scheme != urlsplit(url).scheme
    ):
        canonical_url = url
    return {
        "url": normalize_url(canonical_url),
        "title": title,
        "text": text.strip(),
        "source_system": "public_web",
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }, soup


def fetch_page(url, session=None, max_bytes=2_000_000):
    response = fetch_response(url, session, max_bytes)
    if "text/html" not in response.headers.get("Content-Type", "").lower():
        raise FetchError("Only HTML pages can be ingested through web fetch")
    page, _ = extract_html(response.text, response.url or url)
    return page
