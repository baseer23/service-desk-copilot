"""Measure real graph contexts using explicit isolated deterministic test reasoning.

No Azure inference is dispatched. This measures request size, not model quality or
live throughput. The graph, MCP stdio, retrieval and local persistence are real.
"""

import asyncio
import json
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from backend.app.agent.graph import ServiceDeskAgent
from backend.app.core.config import Settings
from backend.app.mcp.client import DeskMateMCPClient
from backend.app.models.provider_azure_openai import TOKEN_LIMITS, structured_request
from backend.app.testing.provider_stub import StubProvider

SCENARIOS = [
    "Microsoft 365 MFA failure",
    "VPN authentication failure",
    "Outlook email synchronization",
    "Network connectivity loss",
    "Printer installation failure",
    "Microsoft 365 account lockout",
    "Remote desktop connection failure",
    "PDF troubleshooting guidance",
]


@asynccontextmanager
async def open_mcp(settings):
    client = DeskMateMCPClient(settings)
    await client.start()
    try:
        yield client
    finally:
        await client.close()


class Recorder(StubProvider):
    def __init__(self, settings):
        self.settings, self.measurements = settings, []

    async def generate_structured(self, stage, context, schema):
        messages, form = structured_request(self.settings, stage, context, schema)
        serialized = json.dumps(
            {"messages": messages, "response_format": form}, separators=(",", ":")
        )
        output = TOKEN_LIMITS.get(stage, 800)
        estimate = (len(serialized) + 2) // 3 + output
        try:
            import tiktoken

            tokens = len(tiktoken.get_encoding("o200k_base").encode(serialized))
        except ImportError:
            tokens = None
        self.measurements.append(
            {
                "stage": stage,
                "input_characters": len(serialized),
                "input_tokenized": tokens,
                "maximum_output": output,
                "conservative_allocation_tokens": estimate,
            }
        )
        return await super().generate_structured(stage, context, schema)


async def measure():
    with tempfile.TemporaryDirectory(prefix="deskmate-workflow-measure-") as temporary:
        root = Path(temporary)
        settings = Settings(
            _env_file=None,
            test_mode=True,
            generation_provider="stub",
            embed_provider="stub",
            voice_provider="fake",
            case_db_path=root / "cases.db",
            chroma_dir=root / "chroma",
            allow_url_ingest=False,
            max_research_rounds=0,
        )
        recorder = Recorder(settings)
        results = []
        async with open_mcp(settings) as mcp:
            async with AsyncSqliteSaver.from_conn_string(str(root / "checkpoints.db")) as saver:
                await saver.setup()
                agent = ServiceDeskAgent(settings, mcp, recorder, saver)
                await agent.initialize()
                await agent.harness.invoke(
                    "ingest_text",
                    {
                        "title": "Fictional support handbook",
                        "text": (
                            "Fictional Microsoft 365 MFA guidance: record the exact error "
                            "and verify registration before changing anything. "
                            "Fictional VPN guidance: check connected, disconnected "
                            "or authentication error. Outlook sync: record last successful "
                            "sync and account status. Network: check whether one or all "
                            "devices are affected. Printer: verify the print queue and driver. "
                            "Account lockout: record lockout time before changing credentials. "
                            "Remote desktop: record the displayed error and reachability. "
                            "All actions require verification; do not claim resolution "
                            "from unconfirmed reports."
                        ),
                    },
                )
                for index, scenario in enumerate(SCENARIOS):
                    begin = len(recorder.measurements)
                    state, events = await agent.run_request(
                        "measure-" + str(index),
                        "Fictional test: Jake Peralta from B99 reports " + scenario,
                    )
                    turns = 1
                    if state.get("status") == "needs_input":
                        state, more = await agent.run_request(
                            "measure-" + str(index),
                            answer=("Fictional test: the exact displayed error is authentication "
                                    "failed; one workstation is affected."),
                        )
                        events.extend(more)
                        turns += 1
                    results.append(
                        {
                            "scenario": scenario,
                            "turns": turns,
                            "outcome": state.get("status"),
                            "actual_tool_events": sum(
                                (event.get("tool") or "").startswith(("search_", "get_", "fetch_"))
                                for event in events
                            ),
                            "requests": recorder.measurements[begin:],
                        }
                    )
        maximum = max(row["conservative_allocation_tokens"] for row in recorder.measurements)
        return {
            "azure_inference_calls": 0,
            "reasoning": "explicit deterministic test provider",
            "real_graph_mcp_and_temporary_stores": True,
            "scenarios": results,
            "worst_request_allocation_estimate": maximum,
            "suggested_single_request_tpm_floor": ((maximum + 999) // 1000) * 1000,
            "sustained_azure_load_validated": False,
        }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(measure()), indent=2))
