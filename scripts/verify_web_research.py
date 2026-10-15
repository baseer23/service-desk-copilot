"""Opt-in live MCP public-search and page-fetch verification.

This performs read-only public network calls and prints bounded public metadata.
It never writes fetched content to Chroma or prints configuration/secrets.
"""

import argparse
import asyncio
import json
from urllib.parse import urlsplit

from backend.app.core.config import Settings
from backend.app.mcp.client import DeskMateMCPClient


async def verify() -> dict:
    settings = Settings()
    if not settings.enable_public_web_tools:
        raise RuntimeError("Public web tools require ENABLE_PUBLIC_WEB_TOOLS=true")
    client = DeskMateMCPClient(settings)
    try:
        await client.start()
        tools = {tool["name"] for tool in await client.list_tools()}
        if not {"search_web", "fetch_web_page"} <= tools:
            raise RuntimeError("MCP did not discover both public research tools")
        search = await client.call_tool(
            "search_web",
            {"query": "site:learn.microsoft.com AADSTS53003 error documentation", "max_results": 3},
        )
        rows = search.get("items", [])
        candidates = [
            row for row in rows
            if isinstance(row, dict)
            and isinstance(row.get("url"), str)
            and (urlsplit(row["url"]).hostname or "").endswith("learn.microsoft.com")
        ]
        if not candidates:
            raise RuntimeError("Live MCP search returned no authoritative Microsoft source")
        selected = None
        text = ""
        for candidate in candidates:
            fetched = await client.call_tool("fetch_web_page", {"url": candidate["url"]})
            candidate_page = fetched.get("result", fetched)
            candidate_text = (
                candidate_page.get("text", "") if isinstance(candidate_page, dict) else ""
            )
            if len(candidate_text.strip()) >= 120:
                selected, text = candidate, candidate_text
                break
        if selected is None:
            raise RuntimeError("Live MCP sources were found but none could be fetched")
        return {
            "passed": True,
            "tools_discovered": ["fetch_web_page", "search_web"],
            "search_results": len(rows),
            "selected_host": urlsplit(selected["url"]).hostname,
            "selected_title": str(selected.get("title", ""))[:160],
            "fetched_characters": len(text),
        }
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("Public network verification requires --live")
    print(json.dumps(asyncio.run(verify())))


if __name__ == "__main__":
    main()
