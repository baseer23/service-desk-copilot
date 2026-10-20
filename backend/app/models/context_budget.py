"""Conservative, provider-independent model request budgeting.

The persisted investigation remains complete.  Only the transient model view is
packed, so compaction cannot remove evidence from the case or the UI.
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from typing import Any

from backend.app.core.config import Settings


GROQ_MODEL_LIMITS = {
    # https://console.groq.com/docs/model/openai/gpt-oss-120b
    "openai/gpt-oss-120b": 131_072,
    "openai/gpt-oss-20b": 131_072,
    "llama-3.1-8b-instant": 131_072,
    "llama-3.3-70b-versatile": 131_072,
}


@dataclass(frozen=True)
class CompactionReport:
    original_tokens: int
    final_tokens: int
    compacted: bool


def estimate_request_tokens(value: Any) -> int:
    """Estimate tokens conservatively without adding a tokenizer dependency."""
    encoded = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":")).encode()
    return max(1, math.ceil(len(encoded) / 3))


def model_context_window(settings: Settings) -> int:
    if settings.groq_context_window_tokens:
        return settings.groq_context_window_tokens
    return GROQ_MODEL_LIMITS.get(settings.groq_model_name, 32_768)


def _clip(value: Any, string_limit: int) -> Any:
    if isinstance(value, str):
        if len(value) <= string_limit:
            return value
        return value[:string_limit] + "…[compacted]"
    if isinstance(value, list):
        return [_clip(item, string_limit) for item in value]
    if isinstance(value, dict):
        return {key: _clip(item, string_limit) for key, item in value.items()}
    return value


def bounded_context(context: dict[str, Any], max_tokens: int) -> tuple[dict[str, Any], CompactionReport]:
    """Pack recent, relevant context while preserving claims and provenance."""
    original = estimate_request_tokens(context)
    packed = copy.deepcopy(context)

    if isinstance(packed.get("conversation"), list):
        packed["conversation"] = packed["conversation"][-4:]
    if isinstance(packed.get("chat_messages"), list):
        packed["chat_messages"] = packed["chat_messages"][-4:]
    if isinstance(packed.get("evidence"), list):
        # An authoritative inventory is a set-valued answer: truncating it
        # makes the model falsely describe the returned list as incomplete.
        # Preserve a bounded complete inventory alongside the newest ordinary
        # evidence, then let the token-budget loop compact only if necessary.
        inventory = [
            item for item in packed["evidence"]
            if isinstance(item, dict) and item.get("tool") == "list_documents"
        ][-30:]
        recent = [item for item in packed["evidence"] if item not in inventory][-8:]
        packed["evidence"] = recent + inventory
    if isinstance(packed.get("available_tools"), list):
        packed["available_tools"] = packed["available_tools"][:16]
    for key in ("remembered_facts", "confirmed_facts", "human_answers", "missing_information"):
        if isinstance(packed.get(key), list):
            packed[key] = packed[key][-12:]

    protected = {
        key: copy.deepcopy(packed[key])
        for key in ("ticket", "request", "message", "contradictions")
        if key in packed
    }
    for limit in (6000, 3000, 1500, 750, 360):
        packed = _clip(packed, limit)
        packed.update(protected)
        if estimate_request_tokens(packed) <= max_tokens:
            break

    # Drop oldest evidence incrementally before ever touching the current
    # question or contradiction list.
    while estimate_request_tokens(packed) > max_tokens and len(packed.get("evidence", [])) > 1:
        packed["evidence"].pop(0)
    while estimate_request_tokens(packed) > max_tokens and len(packed.get("available_tools", [])) > 1:
        packed["available_tools"].pop()

    final = estimate_request_tokens(packed)
    if final > max_tokens:
        # The protected request itself can be pathological. Bound it last and
        # leave an explicit marker rather than dispatching an oversized call.
        packed = _clip(packed, max(120, max_tokens * 2))
        final = estimate_request_tokens(packed)
    return packed, CompactionReport(original, final, final < original)
