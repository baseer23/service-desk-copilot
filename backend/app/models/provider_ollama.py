import logging
from typing import Any, Dict

import requests

from .provider import LocalModelProvider
from ..core.config import get_settings, DEFAULT_STUB_ANSWER


logger = logging.getLogger(__name__)


class OllamaProvider(LocalModelProvider):
    def __init__(self, model: str | None = None, timeout_sec: int | None = None):
        s = get_settings()
        self.model = model or s.model_name
        self.timeout = timeout_sec or s.model_timeout_sec
        self.base_url = "http://localhost:11434/api/generate"

    def generate(self, prompt: str) -> str:
        payload: Dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 128},
        }
        try:
            resp = requests.post(self.base_url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            text = data.get("response")
            if not text:
                logger.warning("Ollama response missing 'response' field; falling back to stub")
                return DEFAULT_STUB_ANSWER
            return str(text).strip()
        except Exception as e:
            logger.warning("Ollama generate failed: %s", e)
            return DEFAULT_STUB_ANSWER

