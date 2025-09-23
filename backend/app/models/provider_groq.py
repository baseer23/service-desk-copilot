from __future__ import annotations

import logging
from typing import Any, Dict

import requests

from .provider import LocalModelProvider


logger = logging.getLogger(__name__)


class GroqHostedProvider(LocalModelProvider):
    """Adapter for Groq's OpenAI-compatible chat completions endpoint."""

    def __init__(
        self,
        api_key: str | None,
        model_name: str,
        timeout_sec: int,
        api_url: str = "https://api.groq.com/openai/v1/chat/completions",
    ) -> None:
        if not api_key:
            raise ValueError("GROQ_API_KEY must be set for hosted Groq provider")
        self._api_key = api_key
        self._model_name = model_name or "llama-3.1-8b-instant"
        self._timeout = timeout_sec
        self._api_url = api_url.rstrip("/")

    def name(self) -> str:
        return "hosted-groq"

    def generate(self, prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self._model_name,
            "messages": [
                {"role": "system", "content": "You are DeskMate, a precise service desk copilot."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 512,
        }
        try:
            response = requests.post(
                self._api_url,
                headers=headers,
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:  # pragma: no cover - network dependent
            logger.warning("Groq hosted request failed: %s", exc)
            raise RuntimeError("hosted provider request failed") from exc

        content = self._extract_content(data)
        if not content:
            logger.warning("Groq hosted response missing content: %s", data)
            raise RuntimeError("hosted provider response missing content")
        return content.strip()

    def _extract_content(self, data: Dict[str, Any]) -> str | None:
        if not isinstance(data, dict):
            return None
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                delta = first.get("delta")
                if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                    return delta["content"]
                message = first.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    return message["content"]
        if isinstance(data.get("message"), str):
            return data["message"]
        return None
