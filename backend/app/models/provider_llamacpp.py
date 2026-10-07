from __future__ import annotations

import logging
from typing import Any, Dict

import requests

from .provider import LocalModelProvider

logger = logging.getLogger(__name__)


class LlamaCppProvider(LocalModelProvider):
    """Adapter for a llama.cpp OpenAI-compatible completion endpoint."""

    def __init__(self, model_name: str, host: str = "http://localhost:8080", timeout_sec: int = 20) -> None:
        self._model_name = model_name or "default"
        self._host = host.rstrip("/")
        self._timeout = timeout_sec

    def name(self) -> str:
        return "llamacpp"

    def generate(self, prompt: str) -> str:
        payload: Dict[str, Any] = {
            "model": self._model_name,
            "prompt": prompt,
            "stream": False,
            "temperature": 0,
        }
        try:
            response = requests.post(f"{self._host}/completion", json=payload, timeout=self._timeout)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:  # pragma: no cover - network dependent
            logger.warning("llama.cpp request failed: %s", exc)
            raise RuntimeError("llama.cpp request failed") from exc

        text = data.get("content") if isinstance(data, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("llama.cpp response missing content")
        return text.strip()
