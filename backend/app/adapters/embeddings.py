"""Azure-only live embeddings; each deployment has a verified vector namespace."""

import time
from collections.abc import Iterable
from typing import Any, Protocol

from backend.app.core.azure_errors import AzureOperationError, azure_error_code
from backend.app.core.azure_usage import AzureUsageGuard
from backend.app.core.config import Settings


class EmbeddingProvider(Protocol):
    def embed_texts(self, texts: Iterable[str]) -> list[list[float]]: ...


class AzureEmbeddingProvider:
    provider = "azure_openai"

    def __init__(self, settings: Settings, client: Any = None) -> None:
        if not settings.azure_embedding_deployment:
            raise ValueError("AZURE_EMBEDDING_DEPLOYMENT is required for live embeddings")
        self.settings = settings
        self.usage_guard = AzureUsageGuard(settings)
        self.model_name = settings.embed_model_name
        self.dim = settings.azure_embedding_dimensions
        if client is None:
            from backend.app.models.provider_azure_openai import AzureOpenAIProvider

            client = AzureOpenAIProvider(settings).client
        self.client = client

    def embed_texts(self, texts: Iterable[str]) -> list[list[float]]:
        values = list(texts)
        if not values:
            return []
        if any(not isinstance(v, str) for v in values):
            raise ValueError("Embedding input must contain text chunks")
        # Keep the entire document operation below the MCP transport deadline.
        # Never continue paid batches indefinitely after an API caller times out.
        deadline = time.monotonic() + min(self.settings.model_timeout_sec, 45)
        vectors = []
        for start in range(0, len(values), 4):
            while delay := self.usage_guard.request_delay("embedding"):
                if time.monotonic() + delay >= deadline:
                    raise AzureOperationError(
                        "Document embedding deadline exceeded; split the document before indexing",
                        "DOCUMENT_TIMEOUT",
                    )
                time.sleep(min(delay, 1))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AzureOperationError(
                    "Document embedding deadline exceeded", "DOCUMENT_TIMEOUT"
                )
            vectors.extend(self._embed_batch(values[start : start + 4], remaining))
        return vectors

    def _embed_batch(self, values: list[str], timeout: float) -> list[list[float]]:
        reservation = self.usage_guard.reserve(
            "embedding", sum(len(v.encode()) + 32 for v in values)
        )
        response = None
        try:
            self.usage_guard.require_enabled()
            response = self.client.embeddings.create(
                model=self.settings.azure_embedding_deployment,
                input=values,
                dimensions=self.dim,
                timeout=min(self.settings.model_timeout_sec, timeout),
            )
            rows = sorted(response.data, key=lambda row: row.index)
            if len(rows) != len(values) or [row.index for row in rows] != list(range(len(values))):
                raise ValueError("Embedding index mismatch")
            vectors = [row.embedding for row in rows]
            import math

            if any(
                len(vector) != self.dim or not all(math.isfinite(v) for v in vector)
                for vector in vectors
            ):
                raise ValueError("Embedding dimensions or values are invalid")
            return vectors
        except Exception as exc:  # noqa: BLE001 - do not leak credentials or source texts
            code = azure_error_code(exc)
            self.usage_guard.record_check("embeddings", False, code)
            raise AzureOperationError("Azure embedding generation failed", code) from None
        finally:
            self.usage_guard.finish(reservation, getattr(response, "usage", None))


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embed_provider == "stub" and settings.test_mode:
        from backend.app.testing.embeddings import StubEmbeddingProvider

        return StubEmbeddingProvider()
    if settings.embed_provider != "azure_openai":
        raise ValueError("Only Azure embeddings are supported outside isolated tests")
    return AzureEmbeddingProvider(settings)
