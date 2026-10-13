"""Deterministic unit-vector fixtures for isolated tests only."""

import hashlib
import math
import random
from collections.abc import Iterable


class StubEmbeddingProvider:
    """Deterministic vectors for explicitly isolated tests."""

    provider = "stub"
    model_name = "sha256-unit-vector-v1"

    def __init__(self, dim: int = 384):
        self.dim = dim

    def embed_texts(self, texts: Iterable[str]) -> list[list[float]]:
        results = []
        for text in texts:
            rnd = random.Random(hashlib.sha256(text.encode()).digest())  # noqa: S311
            vector = [rnd.uniform(-1, 1) for _ in range(self.dim)]
            norm = math.sqrt(sum(v * v for v in vector)) or 1
            results.append([v / norm for v in vector])
        return results
