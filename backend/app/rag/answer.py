from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List

from backend.app.core.config import DEFAULT_STUB_ANSWER
from backend.app.models.dto import AskResponse, Citation


PROMPT_TEMPLATE = """You are DeskMate, a helpful service desk copilot.
Use the provided knowledge base snippets to answer the question. Always ground your response in the snippets and reference them as [doc_id:chunk_id].

Question:
{question}

Context:
{context}

Answer:
"""


@dataclass
class Responder:
    settings: object
    provider: object

    def answer(self, question: str, planner: Dict[str, object], chunks: List[Dict[str, object]]) -> AskResponse:
        started = time.perf_counter()
        context = self._render_context(chunks)
        prompt = PROMPT_TEMPLATE.format(question=question, context=context)

        model_provider = getattr(self.settings, "model_provider", "stub").lower()
        if model_provider == "stub":
            answer_text = DEFAULT_STUB_ANSWER
        else:
            answer_text = self.provider.generate(prompt)

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
            provider=model_provider,
            question=question,
            citations=citations,
            planner=planner,
            latency_ms=latency_ms,
            confidence=confidence,
        )

    def _render_context(self, chunks: List[Dict[str, object]]) -> str:
        sections = []
        for index, chunk in enumerate(chunks, 1):
            metadata = chunk.get("metadata", {})
            title = metadata.get("title") or metadata.get("doc_id", "unknown")
            sections.append(f"[{index}] ({title})\n{chunk.get('text', '')}")
        return "\n\n".join(sections)

    def _confidence_from_scores(self, scores: List[float]) -> float:
        if not scores:
            return 0.5
        mean_score = sum(scores) / len(scores)
        confidence = 1.0 / (1.0 + mean_score)
        return max(0.1, min(0.99, confidence))
