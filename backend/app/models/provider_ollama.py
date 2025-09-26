from __future__ import annotations

import logging
from typing import Any, Dict

import requests

from .provider import LocalModelProvider

logger = logging.getLogger(__name__)


class OllamaProvider(LocalModelProvider):
    """Adapter for the Ollama local REST API."""

    def __init__(self, model_name: str, host: str, timeout_sec: int) -> None:
        """Initialise the Ollama provider with host, model, and timeout settings."""
        self._model = model_name
        self._host = host.rstrip("/") or "http://localhost:11434"
        self._timeout = timeout_sec

    def name(self) -> str:
        """Return the provider identifier."""

        return "ollama"

    def generate(self, prompt: str) -> str:
        """Call the Ollama REST API and return generated text."""

        url = f"{self._host}/api/generate"
        payload: Dict[str, Any] = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0, "num_predict": 256},
        }
        try:
            response = requests.post(url, json=payload, timeout=self._timeout)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:  # pragma: no cover - network failure path
            logger.warning("Ollama request failed: %s", exc)
            raise RuntimeError("ollama request failed") from exc

        text = data.get("response")
        if not isinstance(text, str) or not text.strip():
            logger.warning("Ollama response missing 'response' field: %s", data)
            raise RuntimeError("ollama response missing text")
        return text.strip()
