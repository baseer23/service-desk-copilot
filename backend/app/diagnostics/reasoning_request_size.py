"""Offline request-size measurement with fictional evidence and real MCP schemas.

Run with PYTHONPATH pointing to an optional installed tiktoken for token estimates.
This does not initialize a model, acquire credentials, or send Azure inference.
Tokenizer counts remain estimates of Azure's quota accounting, not billable usage.
"""

import asyncio
import json
import math
from typing import Any

from pydantic import BaseModel

from backend.app.agent.models import (
    InvestigationDecision,
    InvestigationPlan,
    RequestIntent,
    TicketAnalysis,
    VerificationResult,
)
from backend.app.agent.tools import AgentToolHarness
from backend.app.core.config import get_settings
from backend.app.mcp.client import DeskMateMCPClient
from backend.app.models.instructions import stage_instructions
from backend.app.models.provider_azure_openai import TOKEN_LIMITS, strict_schema, structured_request


async def measure() -> dict[str, Any]:
    settings = get_settings()
    try:
        import tiktoken

        encoding = tiktoken.get_encoding("o200k_base")
    except ImportError:
        encoding = None
    summary = "Fictional test: my VPN client cannot authenticate. What should I check first?"
    client = DeskMateMCPClient(settings)
    try:
        harness = AgentToolHarness(client)
        await harness.initialize()
        context: dict[str, Any] = {
            "ticket": {"summary": summary},
            "workflow": "service_desk",
            "analysis": TicketAnalysis(
                issue_type="VPN authentication", symptoms=summary
            ).model_dump(),
            "available_tools": harness.schemas(),
            "evidence": [
                {
                    "id": "fixture-evidence",
                    "source_system": "local_fixture",
                    "source_type": "document",
                    "tool": "search_knowledge",
                    "reliability": 0.9,
                    "timestamp": "2026-10-13T00:00:00Z",
                    "content": {
                        "text": (
                            "Verify the reported VPN error before changing authentication settings."
                        )
                    },
                }
            ],
        }
        rows = []
        stages: list[tuple[str, type[BaseModel]]] = [
            ("classify_intent", RequestIntent),
            ("analyze_ticket", TicketAnalysis),
            ("plan", InvestigationPlan),
            ("reason", InvestigationDecision),
            ("verify", VerificationResult),
        ]
        for stage, schema in stages:
            supplied: dict[str, Any] = (
                {"request": summary, "knowledge_scope": settings.knowledge_scope}
                if stage == "classify_intent"
                else (
                    {"ticket": {"summary": summary}} if stage == "analyze_ticket" else dict(context)
                )
            )
            if stage == "verify":
                supplied.pop("available_tools")
                supplied["decision"] = InvestigationDecision(
                    evidence_summary="Need the exact error",
                    enough_information=False,
                    question_for_human="What exact VPN error appears?",
                    human_field="vpn_error",
                    confidence=0,
                ).model_dump()
            raw = schema.model_json_schema()
            supported = strict_schema(raw)
            baseline_format = (
                {
                    "type": "json_schema",
                    "json_schema": {"name": schema.__name__, "strict": True, "schema": supported},
                }
                if supported
                else {"type": "json_object"}
            )
            baseline = [
                {
                    "role": "system",
                    "content": stage_instructions(stage, supplied)
                    + "\nRequired JSON schema:\n"
                    + json.dumps(raw),
                },
                {"role": "user", "content": json.dumps(supplied)},
            ]
            messages, form = structured_request(settings, stage, supplied, schema)

            def counts(
                messages: list[dict[str, str]], form: dict[str, Any]
            ) -> dict[str, int | None]:
                text = "\n".join(message["content"] for message in messages) + json.dumps(
                    form, separators=(",", ":")
                )
                return {
                    "characters": len(text),
                    "utf8_bytes": len(text.encode()),
                    "tokenized_estimate": len(encoding.encode(text)) + 16 if encoding else None,
                    "character_estimate": math.ceil(len(text) / 4) + 16,
                }

            rows.append(
                {
                    "stage": stage,
                    "before": counts(baseline, baseline_format),
                    "after": counts(messages, form),
                    "max_output_tokens": min(TOKEN_LIMITS[stage], settings.azure_max_output_tokens),
                }
            )
        return {
            "paid_requests": 0,
            "data": "fictional illustrative workflow",
            "tokenizer": "o200k_base" if encoding else None,
            "quota_accounting_is_estimated": True,
            "stages": rows,
        }
    finally:
        await client.close()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(measure()), indent=2))
