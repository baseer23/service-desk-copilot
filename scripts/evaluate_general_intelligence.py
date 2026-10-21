"""Offline capability-readiness comparison for the frozen 40-case benchmark."""

import json
from pathlib import Path

from backend.app.agent.tools import READ_TOOLS

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "backend/app/tests/general_intelligence_cases.json").read_text())
BASELINE_SHA = "c2b7e607da51838b0a6471483e1d9ce38d22d5d4"
BASELINE_TOOLS = {
    "search_knowledge", "search_graph", "search_web", "search_news", "fetch_web_page",
    "get_current_time", "search_memory", "list_documents",
}


def score(tools):
    completed = [case for case in CASES if set(case["tools"]) <= tools]
    return {
        "cases": len(CASES),
        "capability_ready": len(completed),
        "capability_readiness_rate": round(len(completed) / len(CASES), 4),
        "unavailable_categories": sorted({
            case["category"] for case in CASES if not set(case["tools"]) <= tools
        }),
    }


if __name__ == "__main__":
    print(json.dumps({
        "benchmark": "general_intelligence_cases.json",
        "scope": "offline capability readiness; deterministic factual checks run in pytest",
        "baseline": {"sha": BASELINE_SHA, **score(BASELINE_TOOLS)},
        "candidate": score(READ_TOOLS),
        "live_provider_calls": 0,
        "model_tokens": 0,
        "estimated_model_cost_cad": 0,
    }, indent=2))
