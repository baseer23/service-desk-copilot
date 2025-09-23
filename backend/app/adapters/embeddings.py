from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from typing import Iterable, List, Protocol

import httpx

try:  # pragma: no cover - optional heavy dependency
    from sentence_transformers import SentenceTransformer
except Exception:  # pragma: no cover - capture keras/tf issues as well
    SentenceTransformer = None  # type: ignore


class EmbeddingProvider(Protocol):
    def embed_texts(self, texts: Iterable[str]) -> List[List[float]]:
        ...


@dataclass
class OllamaEmbeddingProvider:
    model: str
    host: str = "http://localhost:11434"
    timeout: float = 10.0

    def embed_texts(self, texts: Iterable[str]) -> List[List[float]]:
        payload = {"model": self.model, "input": list(texts)}
        url = f"{self.host.rstrip('/')}/api/embeddings"
        try:
            response = httpx.post(url, json=payload, timeout=self.timeout)
            response.raise_for_status()
        except httpx.HTTPError as exc:  # pragma: no cover - network dependent
            raise RuntimeError(f"Ollama embeddings request failed: {exc}") from exc
        data = response.json()
        if "data" in data and isinstance(data["data"], list):
            return [item.get("embedding", []) for item in data["data"]]
        if "embedding" in data:
            return [data.get("embedding", [])]
        raise RuntimeError("Invalid response from Ollama embeddings API")


class SentenceTransformersEmbeddingProvider:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        if SentenceTransformer is None:  # pragma: no cover - optional dependency
            raise RuntimeError("sentence-transformers is not installed")
        self._model = SentenceTransformer(model_name)

    def embed_texts(self, texts: Iterable[str]) -> List[List[float]]:
        embeddings = self._model.encode(list(texts), convert_to_numpy=False)
        return [embedding.tolist() for embedding in embeddings]


class StubEmbeddingProvider:
    def __init__(self, dim: int = 384):
        self.dim = dim

    def embed_texts(self, texts: Iterable[str]) -> List[List[float]]:
        results: List[List[float]] = []
        for text in texts:
            seed = hashlib.sha256(text.encode("utf-8")).digest()
            rnd = random.Random(seed)
            vector = [rnd.uniform(-1.0, 1.0) for _ in range(self.dim)]
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            results.append([v / norm for v in vector])
        return results


def get_embedding_provider(settings) -> EmbeddingProvider:
    provider = (getattr(settings, "embed_provider", "stub") or "stub").lower()

    if provider == "ollama":
        return OllamaEmbeddingProvider(model=getattr(settings, "ollama_embed_model", "nomic-embed-text"))

    if provider == "sentence":
        return SentenceTransformersEmbeddingProvider(model_name=getattr(settings, "embed_model_name", "all-MiniLM-L6-v2"))

    if provider == "stub":
        return StubEmbeddingProvider()

    if provider == "auto":
        if _ollama_available(getattr(settings, "ollama_host", "http://localhost:11434")):
            return OllamaEmbeddingProvider(model=getattr(settings, "ollama_embed_model", "nomic-embed-text"))
        try:
            return SentenceTransformersEmbeddingProvider(model_name=getattr(settings, "embed_model_name", "all-MiniLM-L6-v2"))
        except RuntimeError:
            return StubEmbeddingProvider()

    raise ValueError(f"Unknown embedding provider: {provider}")


def _ollama_available(host: str) -> bool:
    url = f"{host.rstrip('/')}/api/tags"
    try:
        response = httpx.get(url, timeout=0.5)
        response.raise_for_status()
        return True
    except httpx.HTTPError:  # pragma: no cover - network dependent
        return False
