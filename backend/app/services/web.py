"""Shared public URL validation, bounded fetching and readable HTML extraction."""

import ipaddress
import socket
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit, urlunsplit
import requests
from bs4 import BeautifulSoup


class UnsafeURL(ValueError):
    pass


class FetchError(RuntimeError):
    pass


def relevant_excerpt(text, query, limit):
    """Bound model input while retaining a passage matching the evidence gap."""
    if len(text) <= limit:
        return text
    needles = set(re.findall(r"[a-zA-Z][a-zA-Z0-9-]{3,}", query))
    needles = sorted(
        needles, key=lambda token: (any(c.isdigit() for c in token), len(token)), reverse=True
    )
    folded = text.casefold()
    position = next(
        (folded.find(token.casefold()) for token in needles if token.casefold() in folded), 0
    )
    if position < limit // 2:
        return text[:limit]
    prefix = text[: min(400, limit // 4)]
    separator = "\n[…]\n"
    width = limit - len(prefix) - len(separator)
    start = max(0, min(position - width // 3, len(text) - width))
    return prefix + separator + text[start : start + width]


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
            redirected = urljoin(url, location)
            # Validate BEFORE any request to the redirect target.
            validate_public_url(redirected)
            if urlsplit(redirected).hostname != urlsplit(url).hostname:
                # Public ingestion has no authenticated browsing state. Do not
                # allow a parent-domain cookie set by one host to influence a
                # validated redirect target (or carry state across origins).
                session.cookies.clear()
            url = redirected
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


def is_www_alias(a, b):
    """True when two parsed URLs differ only as a site's apex and www host."""

    def host(value):
        return (value.hostname or "").lower().removeprefix("www.")

    def port(value):
        return value.port or (443 if value.scheme == "https" else 80)

    return (
        a.scheme == b.scheme
        and port(a) == port(b)
        and host(a) == host(b)
        and a.hostname != b.hostname
        and "www." in {(a.hostname or "")[:4].lower(), (b.hostname or "")[:4].lower()}
    )


def same_site_url(url, origin_url):
    """``url`` on ``origin_url``'s host (apex/www aliases rewritten), else None."""
    target, origin = urlsplit(url), urlsplit(origin_url)
    if (target.scheme, target.netloc) == (origin.scheme, origin.netloc):
        return url
    if is_www_alias(origin, target):
        return urlunsplit((origin.scheme, origin.netloc, target.path, target.query, ""))
    return None


VOLATILE_TAGS = ["script", "style", "noscript", "template"]
VOLATILE_ATTRIBUTES = {"nonce", "integrity", "data-nonce", "data-csrf", "data-cf-beacon"}


def stable_html_fingerprint(html):
    """Bytes identifying a page's content: scripts, styles, comments and nonces removed.

    Re-crawling an unchanged page whose markup carries per-request tokens then
    resolves to the same document instead of a new version.
    """
    from bs4 import Comment

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(VOLATILE_TAGS):
        tag.decompose()
    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()
    for meta in soup.find_all("meta"):
        if "csrf" in str(meta.get("name", "")).lower():
            meta.decompose()
    for tag in soup.find_all(True):
        for attribute in VOLATILE_ATTRIBUTES & set(tag.attrs):
            del tag[attribute]
    return re.sub(r"\s+", " ", str(soup)).strip().encode("utf-8")


def extract_html(html, url):
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else url
    for tag in soup(
        ["script", "style", "noscript", "template", "nav", "footer", "form", "aside", "iframe"]
    ):
        tag.decompose()
    article = soup.find("article") or soup.find("main") or soup.body or soup
    blocks = article.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "tr"])
    blocks = [b for b in blocks if not b.find_parent(["p", "li", "pre", "tr"])]
    text = (
        "\n\n".join(
            " | ".join(
                cell.get_text(" ", strip=True) for cell in b.find_all(["th", "td"], recursive=False)
            )
            if b.name == "tr"
            else b.get_text(" ", strip=True)
            for b in blocks
        )
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


MAX_PAGE_LINKS = 500
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def same_origin_links(html, url):
    """Normalized same-site links on a page (navigation included), self excluded.

    Links to the site's apex/www alias are rewritten to the page's own host.
    """
    links = []
    for anchor in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        target = same_site_url(normalize_url(urljoin(url, anchor["href"].strip())), url)
        if target and target != normalize_url(url) and target not in links:
            links.append(target)
            if len(links) >= MAX_PAGE_LINKS:
                break
    return links


def html_extraction(html, url):
    """Extract one HTML page in the layout shape Azure Document Intelligence produces.

    Headings become title/sectionHeading paragraphs, paragraphs, list items and
    preformatted blocks become body paragraphs, and tables keep their cells, so
    split_structured and the graph publisher build the same Section, Paragraph,
    Table, TableCell and Chunk objects they build for PDFs. Everything is page 1.
    """
    from backend.app.services.document_processing import _sections

    links = same_origin_links(html, url)
    page, soup = extract_html(html, url)
    article = soup.find("article") or soup.find("main") or soup.body or soup
    paragraphs, tables, seen_title = [], [], False
    for block in article.find_all([*HEADING_TAGS, "p", "li", "pre", "table"]):
        if block.find_parent(["p", "li", "pre", "table"]):
            continue
        if block.name == "table":
            cells = [
                {
                    "row": row_index, "column": column_index,
                    "kind": "columnHeader" if cell.name == "th" else "content",
                    "text": cell.get_text(" ", strip=True), "spans": [],
                }
                for row_index, row in enumerate(block.find_all("tr"))
                for column_index, cell in enumerate(row.find_all(["th", "td"], recursive=False))
            ]
            if any(cell["text"] for cell in cells):
                tables.append({
                    "id": f"table-1-{len(tables)}", "page": 1,
                    "row_count": 1 + max(cell["row"] for cell in cells),
                    "column_count": 1 + max(cell["column"] for cell in cells),
                    "cells": cells,
                })
            continue
        text = block.get_text(" ", strip=True)
        if not text:
            continue
        role = None
        if block.name in HEADING_TAGS:
            role = "sectionHeading" if seen_title or block.name != "h1" else "title"
            seen_title = seen_title or block.name == "h1"
        position = len(paragraphs)
        paragraphs.append({
            "id": f"paragraph-1-{position}", "page": 1, "text": text,
            "role": role, "spans": [], "position": position,
        })
    if not page["text"] and not tables:
        raise ValueError("The document contains no readable text")
    return {
        "pages": [{"page": 1, "text": page["text"], "markdown": page["text"], "spans": []}],
        "paragraphs": paragraphs,
        "sections": _sections(paragraphs),
        "tables": tables,
        "figures": [],
        "method": "html_structure",
        "metadata": {
            "format": "text/html", "page_count": 1, "table_count": len(tables),
            "classification": "web_page", "classification_method": "html_structure",
            "structured": True, "image_understanding": "not_applicable",
            "title": page["title"], "canonical_url": page["url"], "links": links,
        },
    }


def merge_html_analysis(analysis, local):
    """Azure layout analysis of a web page in the saved extraction schema.

    Azure supplies paragraphs, roles and tables; the local parse supplies what
    Azure does not return (links, canonical URL, title). Locally parsed tables
    are kept when Azure's are missing or disagree, and that choice is recorded.
    Everything is page 1, as for a locally parsed page.
    """
    from backend.app.services.document_processing import _sections

    paragraphs = [
        {**paragraph, "id": f"paragraph-1-{position}", "page": 1, "position": position,
         "bounding_regions": []}
        for position, paragraph in enumerate(analysis.get("paragraphs") or [])
        if str(paragraph.get("text") or "").strip()
    ]
    metadata = dict(local.get("metadata") or {})
    if not paragraphs:
        metadata["azure_analysis"] = "empty_result_local_extraction_kept"
        return {**local, "metadata": metadata}

    def cell_texts(tables):
        return sorted(
            re.sub(r"\s+", " ", str(cell.get("text") or "")).strip().casefold()
            for table in tables for cell in table.get("cells", [])
            if str(cell.get("text") or "").strip()
        )

    azure_tables = [
        {**table, "id": f"table-1-{index}", "page": 1, "bounding_regions": []}
        for index, table in enumerate(analysis.get("tables") or [])
    ]
    local_tables = local.get("tables") or []
    if not local_tables and not azure_tables:
        tables, source, note = [], "none", "no_tables"
    elif not azure_tables:
        tables, source, note = local_tables, "local", "azure_returned_no_tables"
    elif cell_texts(azure_tables) != cell_texts(local_tables) or len(azure_tables) != len(
        local_tables
    ):
        tables, source, note = local_tables, "local", "azure_tables_disagree_with_page"
    else:
        tables, source, note = azure_tables, "azure", "azure_tables_match_page"
    text = "\n".join(str(page.get("text") or "") for page in analysis.get("pages") or []).strip()
    text = text or "\n\n".join(paragraph["text"] for paragraph in paragraphs)
    metadata.update(
        classification_method="azure_document_intelligence_layout",
        azure_analysis="analysed", tables_source=source, tables_note=note,
        # Document Intelligence bills HTML per 3,000 characters.
        page_units_estimate=max(1, -(-len(text) // 3000)),
    )
    return {
        "pages": [{"page": 1, "text": text, "markdown": text, "spans": []}],
        "paragraphs": paragraphs,
        "sections": _sections(paragraphs),
        "tables": tables,
        "figures": [],
        "method": "document_intelligence",
        "metadata": metadata,
    }


def fetch_page(url, session=None, max_bytes=2_000_000):
    response = fetch_response(url, session, max_bytes)
    if "text/html" not in response.headers.get("Content-Type", "").lower():
        raise FetchError("Only HTML pages can be ingested through web fetch")
    page, _ = extract_html(response.text, response.url or url)
    return page
