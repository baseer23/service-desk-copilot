from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

from .provider import LocalModelProvider


logger = logging.getLogger(__name__)


class LlamaCppProvider(LocalModelProvider):
    """Adapter for the llama.cpp REST-compatible server."""

    def __init__(self, host: str, model: Optional[str], timeout_sec: int) -> None:
        self._host = host.rstrip("/") or "http://localhost:8080"
        self._model = model
        self._timeout = timeout_sec

    def name(self) -> str:
        return "llamacpp"

    def generate(self, prompt: str) -> str:
        url = f"{self._host}/completion"
        payload: Dict[str, Any] = {
            "prompt": prompt,
            "temperature": 0,
            "stream": False,
            "n_predict": 256,
        }
        if self._model:
            payload["model"] = self._model

        try:
            response = requests.post(url, json=payload, timeout=self._timeout)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:  # pragma: no cover - network failure
            logger.warning("llama.cpp request failed: %s", exc)
            raise RuntimeError("llama.cpp request failed") from exc

        text = self._extract_text(data)
        if not text:
            logger.warning("llama.cpp response missing text: %s", data)
            raise RuntimeError("llama.cpp response missing text")
        return text.strip()

    def _extract_text(self, data: Dict[str, Any]) -> str | None:
        if not isinstance(data, dict):
            return None
        if isinstance(data.get("content"), str):
            return data["content"]
        if isinstance(data.get("text"), str):
            return data["text"]
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, dict):
                if isinstance(choice.get("text"), str):
                    return choice["text"]
                message = choice.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    return message["content"]
        return None
