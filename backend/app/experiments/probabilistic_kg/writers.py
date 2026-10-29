"""Two transports for the same write plan: the Python driver (B) and neo4j-mcp (C).

Both execute the identical statements from ``mutations.compile_plan`` and
verify the result by reading back the expected IDs. Differences measured here
are transport differences only: transactions, round trips, atomicity and
failure behaviour. Graph intelligence comes from the reasoning and validation
that produced the plan, not from the transport.
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from backend.app.experiments.probabilistic_kg.mutations import (
    PIPELINE,
    READ_TEMPLATES,
    TEMPLATES,
    Statement,
    WritePlan,
)


@dataclass
class WriteReport:
    transport: str
    statements: int = 0
    write_calls: int = 0
    read_calls: int = 0
    transactions: int = 0
    written: dict[str, int] = field(default_factory=dict)
    verified: dict[str, tuple[int, int]] = field(default_factory=dict)  # label -> (expected, present)
    ok: bool = False
    atomic: bool = False
    error: str | None = None
    applied_before_error: int = 0
    seconds: float = 0.0

    def verification_passed(self):
        return all(expected == present for expected, present in self.verified.values())


def _params(plan: WritePlan, statement: Statement) -> dict:
    return {**statement.params, "tenant": plan.tenant, "pipeline": PIPELINE}


def _rows(output):
    """Normalise neo4j-mcp / driver results to a list of row dicts."""
    envelope = ("result", "results", "rows")
    while True:
        # The external MCP client wraps a JSON object result in a list.
        if (isinstance(output, list) and len(output) == 1 and isinstance(output[0], dict)
                and any(key in output[0] for key in envelope)):
            output = output[0]
        if isinstance(output, dict) and any(key in output for key in envelope):
            output = output.get("rows", output.get("result", output.get("results")))
            continue
        break
    if isinstance(output, dict):
        return [output]
    return [row for row in output or [] if isinstance(row, dict)]


class DriverGraphWriter:
    """Approach B: one write transaction through the Python Neo4j driver."""

    transport = "python_driver"

    def __init__(self, driver, database: str | None = None):
        self.driver, self.database = driver, database

    def write(self, plan: WritePlan) -> WriteReport:
        report = WriteReport(self.transport, statements=len(plan.statements), atomic=True)
        started = time.perf_counter()

        def work(tx):
            written = {}
            for statement in plan.statements:
                record = tx.run(statement.query, _params(plan, statement)).single()
                written[statement.template] = written.get(statement.template, 0) + int(
                    (record or {}).get("written", 0)
                )
            return written

        try:
            with self.driver.session(database=self.database) as session:
                report.written = session.execute_write(work)
                report.transactions += 1
                report.write_calls = len(plan.statements)
                report.verified = session.execute_read(lambda tx: self._verify(tx, plan))
                report.transactions += 1
                report.read_calls = len(report.verified)
        except Exception as exc:  # noqa: BLE001 - reported, never retried
            report.error = type(exc).__name__
        report.ok = report.error is None and report.verification_passed()
        report.seconds = time.perf_counter() - started
        return report

    @staticmethod
    def _verify(tx, plan):
        verified = {}
        for label, ids in plan.expected.items():
            if not ids:
                continue
            record = tx.run(READ_TEMPLATES[f"verify:{label}"],
                            {"tenant": plan.tenant, "ids": ids}).single()
            verified[label] = (len(ids), int((record or {}).get("present", 0)))
        return verified

    def export(self, tenant, limit=10000):
        with self.driver.session(database=self.database) as session:
            def read(tx):
                params = {"tenant": tenant, "pipeline": PIPELINE, "limit": limit}
                return (tx.run(READ_TEMPLATES["export:nodes"], params).data(),
                        tx.run(READ_TEMPLATES["export:edges"], params).data())
            return session.execute_read(read)


class UnauthorizedStatement(PermissionError):
    pass


class McpGraphWriter:
    """Approach C: the same statements through official neo4j-mcp write-cypher.

    Only exact template text is sent; anything else (including model-written
    Cypher) is refused before reaching MCP. Each write-cypher call is its own
    transaction, so a failure part-way leaves earlier statements applied. The
    statements are MERGE-only and safe to re-run by an operator, but a failed
    or ambiguous write is never retried automatically.
    """

    transport = "neo4j_mcp_write_cypher"

    def __init__(self, client):
        self.client = client

    @staticmethod
    def authorize(statement: Statement):
        if TEMPLATES.get(statement.template) != statement.query:
            raise UnauthorizedStatement("Only allowlisted write templates may be sent")

    async def write(self, plan: WritePlan) -> WriteReport:
        report = WriteReport(self.transport, statements=len(plan.statements), atomic=False)
        started = time.perf_counter()
        try:
            for statement in plan.statements:
                self.authorize(statement)
                output = await self.client.call_tool(
                    "write-cypher", {"query": statement.query, "params": _params(plan, statement)}
                )
                report.write_calls += 1
                report.transactions += 1
                report.applied_before_error += 1
                rows = _rows(output)
                report.written[statement.template] = report.written.get(statement.template, 0) + int(
                    (rows[0] if rows else {}).get("written", 0)
                )
            for label, ids in plan.expected.items():
                if not ids:
                    continue
                output = await self.client.call_tool("read-cypher", {
                    "query": READ_TEMPLATES[f"verify:{label}"],
                    "params": {"tenant": plan.tenant, "ids": ids},
                })
                report.read_calls += 1
                rows = _rows(output)
                report.verified[label] = (len(ids), int((rows[0] if rows else {}).get("present", 0)))
        except UnauthorizedStatement:
            raise
        except Exception as exc:  # noqa: BLE001 - reported, never retried
            report.error = type(exc).__name__
        report.ok = report.error is None and report.verification_passed()
        report.seconds = time.perf_counter() - started
        return report

    async def export(self, tenant, limit=10000):
        params = {"tenant": tenant, "pipeline": PIPELINE, "limit": limit}
        nodes = _rows(await self.client.call_tool(
            "read-cypher", {"query": READ_TEMPLATES["export:nodes"], "params": params}))
        edges = _rows(await self.client.call_tool(
            "read-cypher", {"query": READ_TEMPLATES["export:edges"], "params": params}))
        return nodes, edges


WRITER_TOOLS = {"write-cypher", "read-cypher", "get-schema"}
EXPERIMENT_ENV = {
    "NEO4J_MCP_URI": "DESKMATE_EXP_NEO4J_URI",
    "NEO4J_MCP_USERNAME": "DESKMATE_EXP_NEO4J_USERNAME",
    "NEO4J_MCP_PASSWORD": "DESKMATE_EXP_NEO4J_PASSWORD",
    "NEO4J_MCP_DATABASE": "DESKMATE_EXP_NEO4J_DATABASE",
}


def build_writer_env(settings, parent_env) -> dict[str, str]:
    """A write-enabled neo4j-mcp environment for an isolated experiment database.

    Credentials come only from DESKMATE_EXP_NEO4J_*; the production read-only
    connection and its credentials are never reused, and the experiment URI
    must differ from the application's configured Neo4j URI.
    """
    from backend.app.mcp.client import MCPConnectionError

    child = {key: value for key, value in parent_env.items()
             if key in {"HOME", "LANG", "PATH", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR"}}
    for child_key, source_key in EXPERIMENT_ENV.items():
        value = parent_env.get(source_key, "").strip()
        if not value and child_key != "NEO4J_MCP_DATABASE":
            raise MCPConnectionError("Experiment Neo4j credentials are not configured")
        child[child_key] = value or "neo4j"
    production = (getattr(settings, "neo4j_uri", "") or "").strip().rstrip("/")
    if production and child["NEO4J_MCP_URI"].rstrip("/") == production:
        raise MCPConnectionError("The experiment must not write to the application's Neo4j")
    child["NEO4J_MCP_READ_ONLY"] = "false"
    child["NEO4J_MCP_TELEMETRY"] = "false"
    return child


def restricted_writer_client(settings, *, command="neo4j-mcp", args=(), env_builder=None,
                             cwd=None):
    """A separate MCP connection that only the experiment's writer uses."""
    from mcp.client.stdio import StdioServerParameters, stdio_client

    from backend.app.mcp.client import PROJECT_ROOT, MCPToolError
    from backend.app.mcp.multi import ExternalMCPClient, ServerSpec

    spec = ServerSpec(name="neo4j_exp_writer", command=command, args=list(args),
                      read_tools=[], workflows=["knowledge"])

    class RestrictedWriterClient(ExternalMCPClient):
        @asynccontextmanager
        async def transport(self):
            environment = (env_builder or build_writer_env)(self.settings, os.environ)
            params = StdioServerParameters(command=self.spec.command, args=self.spec.args,
                                           env=environment, cwd=cwd or str(PROJECT_ROOT))
            with open(os.devnull, "w") as log:
                async with stdio_client(params, errlog=log) as streams:
                    yield streams

        async def call_tool(self, tool_name, params):
            if tool_name not in WRITER_TOOLS | {"__list_tools__"}:
                raise MCPToolError("The experiment writer only exposes Cypher read/write tools", 403)
            return await super().call_tool(tool_name, params)

    return RestrictedWriterClient(settings, spec)
