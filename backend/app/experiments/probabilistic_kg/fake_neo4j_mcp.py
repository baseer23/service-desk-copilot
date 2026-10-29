"""Offline stand-in for the official neo4j-mcp server (tests only).

A real MCP stdio server exposing neo4j-mcp's tool names (get-schema,
read-cypher, write-cypher) and read-only annotations, executing only the
experiment's allowlisted templates against an in-memory store. It honours
NEO4J_MCP_READ_ONLY like the real server: when "true", write-cypher is not
offered. FAKE_NEO4J_SEED points to the structural graph to start from.
"""

from __future__ import annotations

import json
import os

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from backend.app.experiments.probabilistic_kg.sink import TemplateGraphStore

seed_path = os.environ.get("FAKE_NEO4J_SEED")
store = TemplateGraphStore.from_seed(json.loads(open(seed_path).read())) if seed_path else (
    TemplateGraphStore()
)
read_only = os.environ.get("NEO4J_MCP_READ_ONLY", "true").casefold() == "true"
server = FastMCP("neo4j-mcp-fake")


@server.tool(name="get-schema", annotations=ToolAnnotations(readOnlyHint=True))
def get_schema() -> dict:
    labels = sorted({label for node in store.nodes.values() for label in node["labels"]})
    relationships = sorted({key[1] for key in store.edges})
    return {"labels": labels, "relationship_types": relationships}


@server.tool(name="read-cypher", annotations=ToolAnnotations(readOnlyHint=True))
def read_cypher(query: str, params: dict | None = None) -> dict:
    from backend.app.experiments.probabilistic_kg.mutations import READ_TEMPLATES

    if query not in READ_TEMPLATES.values():
        raise ValueError("read-cypher received a query that is not a read template")
    return {"rows": store.run(query, params or {})}


if not read_only:
    @server.tool(
        name="write-cypher",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True),
    )
    def write_cypher(query: str, params: dict | None = None) -> dict:
        return {"rows": store.run(query, params or {})}


if __name__ == "__main__":
    server.run()
