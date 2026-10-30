# Official Neo4j MCP integration

DeskMate uses the [official Neo4j MCP server](https://github.com/neo4j/mcp) as an optional, local stdio child process. The verified macOS installation is Homebrew `neo4j-mcp` 1.6.0. FastAPI owns its lifetime; do not start another daemon or expose it over HTTP.

## Installation and configuration

Install with `brew install neo4j-mcp`. Where Homebrew is unavailable, the upstream project's supported distribution for the relevant platform may be used; configure `MCP_SERVERS` with its absolute executable and arguments. Verify the selected build with `neo4j-mcp -v` and consult the [current installation guide](https://neo4j.com/docs/mcp/current/installation/).

Keep the Aura URI, username, password, and database in the private `.env` as `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, and `NEO4J_DATABASE`. DeskMate maps these validated settings only into the child process as `NEO4J_MCP_URI`, `NEO4J_MCP_USERNAME`, `NEO4J_MCP_PASSWORD`, and `NEO4J_MCP_DATABASE`. It forces `NEO4J_MCP_READ_ONLY=true` and `NEO4J_MCP_TELEMETRY=false`. Never place credentials in `MCP_SERVERS`. neo4j-mcp 1.6.x reads only the `NEO4J_MCP_*` names (see `neo4j-mcp --help`); plain `NEO4J_*` names are deprecated aliases and are never set for the child process.

Example registration:

```json
[{"name":"neo4j","transport":"stdio","command":"/opt/homebrew/bin/neo4j-mcp","args":[],"environment":{},"read_tools":["get-schema","read-cypher"],"workflows":["knowledge","service_desk"],"allowed_tenants":[]}]
```

Run the adapter-only check with `.venv/bin/python scripts/verify_neo4j_mcp.py`. Run live discovery and the bounded read-only checks with `.venv/bin/python scripts/verify_neo4j_mcp.py --live`. Expected public tool names are `neo4j__get-schema` and `neo4j__read-cypher`; `neo4j__write-cypher` must be absent.

The live verifier runs only `get-schema`, `RETURN 1 AS ok`, and `MATCH (n) RETURN count(n) AS node_count`. An empty graph is valid. It never creates or changes data.

## Storage and security boundary

ChromaDB remains DeskMate's local vector store and is not replaced by Aura. Original documents, extracted text, vectors, SQLite state, and checkpoints remain local. This integration neither uploads documents/chunks nor backfills graph data.

The pre-existing direct graph repository can write chunk text to the `Chunk.text` property during an explicitly authorized ingestion path. That represents potential remote document-content exposure when Aura is configured; this MCP integration does not call that writer, migrate it, or alter existing graph data.

Only discovered tools that are both explicitly allowlisted and annotated `readOnlyHint=true` are model-visible. Destructive annotations are denied. Production additionally fails closed unless the current signed tenant appears in `allowed_tenants`; the default empty list intentionally grants no production tenant. A production deployment still requires a tenant-isolated, minimally privileged database identity and a constrained query boundary before enabling arbitrary read Cypher.

## Troubleshooting

- Authentication: confirm the private password and read-only database user; do not print them. Invalid credentials should be fixed rather than repeatedly retried.
- DNS/TLS: Aura requires its `neo4j+s://` hostname and valid certificate resolution. Check local DNS, proxy, and firewall policy.
- Provisioning: a new or paused Aura instance may take time. The verifier retries bounded connection failures for about three minutes.
- Missing tools: confirm read-only mode, server version, the two `read_tools` grants, and the tool annotations. A write tool is intentionally unavailable.
- Empty schema: an empty database can return no schema while `RETURN 1 AS ok` still proves the connection.
