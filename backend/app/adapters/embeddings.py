from __future__ import annotations
import hashlib
import math
import random
from typing import Iterable, Protocol


class EmbeddingProvider(Protocol):
    def embed_texts(self, texts: Iterable[str]) -> list[list[float]]: ...


class SentenceTransformersEmbeddingProvider:
    provider = "sentence"
    model_name = "all-MiniLM-L6-v2"
    dim = 384

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        if model_name != self.model_name:
            raise ValueError("Only all-MiniLM-L6-v2 embeddings are supported")
        self._model = None

    def embed_texts(self, texts):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        try:
            return self._model.encode(list(texts), convert_to_numpy=True).tolist()
        except Exception as exc:
            raise RuntimeError("SentenceTransformers embedding failed") from exc


class StubEmbeddingProvider:
    """Deterministic vectors for explicitly isolated tests."""

    provider = "stub"
    model_name = "sha256-unit-vector-v1"

    def __init__(self, dim: int = 384):
        self.dim = dim

    def embed_texts(self, texts):
        results = []
        for text in texts:
            rnd = random.Random(hashlib.sha256(text.encode()).digest())
            vector = [rnd.uniform(-1, 1) for _ in range(self.dim)]
            norm = math.sqrt(sum(v * v for v in vector)) or 1
            results.append([v / norm for v in vector])
        return results


def get_embedding_provider(settings):
    if settings.embed_provider == "sentence":
        return SentenceTransformersEmbeddingProvider(settings.embed_model_name)
    if settings.embed_provider == "stub" and settings.test_mode:
        return StubEmbeddingProvider()
    raise ValueError("Unsupported embedding provider or test mode is disabled")
