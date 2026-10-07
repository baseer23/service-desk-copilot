from __future__ import annotations

import logging
from typing import Any, Dict

import requests

from .provider import LocalModelProvider

logger = logging.getLogger(__name__)


class OllamaProvider(LocalModelProvider):
    """Adapter for a local Ollama chat endpoint."""

    def __init__(self, model_name: str, host: str = "http://localhost:11434", timeout_sec: int = 20) -> None:
        self._model_name = model_name or "phi3:mini"
        self._host = host.rstrip("/")
        self._timeout = timeout_sec

    def name(self) -> str:
        return "ollama"

    def generate(self, prompt: str) -> str:
        payload: Dict[str, Any] = {
            "model": self._model_name,
            "prompt": prompt,
            "stream": False,
        }
        try:
            response = requests.post(f"{self._host}/api/generate", json=payload, timeout=self._timeout)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:  # pragma: no cover - network dependent
            logger.warning("Ollama request failed: %s", exc)
            raise RuntimeError("ollama request failed") from exc

        text = data.get("response") if isinstance(data, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("ollama response missing content")
        return text.strip()
