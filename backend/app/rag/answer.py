"""Response composition for Ask requests."""

from __future__ import annotations

import logging
import textwrap
import time
from dataclasses import dataclass
from typing import Dict, List

from backend.app.core.config import DEFAULT_STUB_ANSWER
from backend.app.models.dto import AskResponse, Citation
from backend.app.models.provider import LocalModelProvider


logger = logging.getLogger(__name__)
FALLBACK_PREFIX = "Local model unavailable; falling back to stub. "


def compose_prompt(question: str, chunks: List[Dict[str, object]]) -> str:
    """Build the final prompt sent to the language model."""

    header = (
        "You are DeskMate, a helpful service desk copilot.\n"
        "Use ONLY the provided context to answer.\n"
        "Cite supporting evidence with [doc_id:chunk_id] tags that already exist in the context."
    )

    context_lines = []
    for chunk in chunks:
        metadata = chunk.get("metadata", {})
        doc_id = metadata.get("doc_id", "unknown")
        chunk_id = chunk.get("id", "")
        title = metadata.get("title") or doc_id
        snippet = (chunk.get("text") or "").strip().replace("\n", " ")
        snippet = textwrap.shorten(snippet, width=500, placeholder="…")
        context_lines.append(f"[{doc_id}:{chunk_id}] {title}: {snippet}")

    context_block = "\n".join(context_lines) if context_lines else "(no context available)"
    prompt = (
        f"{header}\n\nContext:\n{context_block}\n\n"
        f"Question: {question.strip()}\nAnswer:"
    )
    return prompt


@dataclass
class Responder:
    settings: object
    provider: LocalModelProvider

    def answer(self, question: str, planner: Dict[str, object], chunks: List[Dict[str, object]]) -> AskResponse:
        started = time.perf_counter()
        prompt = compose_prompt(question, chunks)

        provider_name = self.provider.name()
        use_stub = provider_name == "stub"
        answer_text: str

        if use_stub:
            answer_text = DEFAULT_STUB_ANSWER
        else:
            try:
                answer_text = self.provider.generate(prompt)
            except Exception as exc:  # pragma: no cover - defensive fallback
                logger.warning("Provider %s failed, using stub fallback: %s", provider_name, exc)
                answer_text = f"{FALLBACK_PREFIX}{DEFAULT_STUB_ANSWER}"

        citations = [
            Citation(
                doc_id=chunk.get("metadata", {}).get("doc_id", "unknown"),
                chunk_id=chunk.get("id", ""),
                score=float(chunk.get("score", 0.0) or 0.0),
                title=chunk.get("metadata", {}).get("title"),
                snippet=chunk.get("text"),
            )
            for chunk in chunks
        ]

        confidence = self._confidence_from_scores([citation.score for citation in citations])
        latency_ms = int((time.perf_counter() - started) * 1000)

        return AskResponse(
            answer=answer_text,
            provider=provider_name,
            question=question,
            citations=citations,
            planner=planner,
            latency_ms=latency_ms,
            confidence=confidence,
        )

    def _confidence_from_scores(self, scores: List[float]) -> float:
        if not scores:
            return 0.5
        mean_score = sum(scores) / len(scores)
        confidence = 1.0 / (1.0 + mean_score)
        return max(0.1, min(0.99, confidence))
