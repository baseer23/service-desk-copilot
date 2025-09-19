import logging
from typing import Any, Dict

import requests

from .provider import LocalModelProvider
from ..core.config import get_settings, DEFAULT_STUB_ANSWER


logger = logging.getLogger(__name__)


class LlamaCppProvider(LocalModelProvider):
    def __init__(self, timeout_sec: int | None = None):
        s = get_settings()
        self.timeout = timeout_sec or s.model_timeout_sec
        self.base_url = "http://localhost:8080/completion"

    def generate(self, prompt: str) -> str:
        payload: Dict[str, Any] = {"prompt": prompt, "n_predict": 128, "temperature": 0.2}
        try:
            resp = requests.post(self.base_url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            # Try a few common fields
            text = data.get("content") or data.get("text")
            if not text and isinstance(data, dict):
                choices = data.get("choices")
                if isinstance(choices, list) and choices:
                    text = choices[0].get("text")
            if not text:
                logger.warning("llama.cpp response missing text; falling back to stub")
                return DEFAULT_STUB_ANSWER
            return str(text).strip()
        except Exception as e:
            logger.warning("llama.cpp generate failed: %s", e)
            return DEFAULT_STUB_ANSWER

