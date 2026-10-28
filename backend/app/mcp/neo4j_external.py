"""Private child-process environment for the official Neo4j MCP server."""

from __future__ import annotations

from collections.abc import Mapping

from backend.app.mcp.client import MCPConnectionError


_PASSTHROUGH = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LOGNAME",
    "PATH",
    "SHELL",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "SYSTEMROOT",
    "TMPDIR",
    "USER",
}


def build_neo4j_stdio_env(settings, parent_env: Mapping[str, str]) -> dict[str, str]:
    """Build an isolated read-only environment without mutating ``parent_env``."""
    uri = settings.neo4j_uri.strip()
    username = settings.neo4j_user.strip()
    database = settings.neo4j_database.strip()
    password = settings.neo4j_password.get_secret_value()
    if not uri or not username or not password or not database:
        raise MCPConnectionError("Neo4j MCP credentials are missing or incomplete")

    child = {key: value for key, value in parent_env.items() if key in _PASSTHROUGH}
    child.update(
        {
            "NEO4J_URI": uri,
            "NEO4J_USERNAME": username,
            "NEO4J_PASSWORD": password,
            "NEO4J_DATABASE": database,
            "NEO4J_READ_ONLY": "true",
            "NEO4J_TELEMETRY": "false",
        }
    )
    return child
