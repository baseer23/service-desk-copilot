"""Local SentenceTransformers embeddings with explicit provenance and failure states."""

import math
import threading
from collections.abc import Iterable
from typing import Any, Protocol

from langchain_core.embeddings import Embeddings

from backend.app.core.config import Settings


class EmbeddingProvider(Protocol):
    def embed_texts(self, texts: Iterable[str]) -> list[list[float]]: ...


class SentenceTransformerEmbeddingProvider(Embeddings):
    provider = "sentence_transformers"
    _models: dict[str, Any] = {}
    _load_lock = threading.Lock()
    _encode_lock = threading.Lock()

    def __init__(self, settings: Settings, model: Any = None):
        self.model_name = settings.embed_model_name
        if self.model_name != "all-MiniLM-L6-v2":
            raise ValueError("Local embeddings require all-MiniLM-L6-v2")
        self.dim = 384
        self._injected_model = model

    def _model(self):
        if self._injected_model is not None:
            return self._injected_model
        with self._load_lock:
            if self.model_name not in self._models:
                try:
                    from sentence_transformers import SentenceTransformer

                    # Fixed public model, CPU-only; never execute repository code.
                    model = SentenceTransformer(
                        "sentence-transformers/" + self.model_name,
                        device="cpu", trust_remote_code=False,
                    )
                    dimension = getattr(model, "get_embedding_dimension", None) or model.get_sentence_embedding_dimension
                    if dimension() != self.dim:
                        raise ValueError("Unexpected local embedding dimensions")
                    self._models[self.model_name] = model
                except Exception:
                    raise RuntimeError(
                        "Local embedding model could not be loaded; check the model cache or download access"
                    ) from None
            return self._models[self.model_name]

    def embed_texts(self, texts: Iterable[str]) -> list[list[float]]:
        values = list(texts)
        if not values:
            return []
        if any(not isinstance(value, str) for value in values):
            raise ValueError("Embedding input must contain text chunks")
        try:
            # Sharing one CPU model avoids duplicate memory/downloads in a process.
            # The MCP capability runs this synchronous work on its worker thread.
            with self._encode_lock:
                result = self._model().encode(
                    values, batch_size=32, normalize_embeddings=True,
                    show_progress_bar=False, convert_to_numpy=True,
                )
            vectors = result.tolist() if hasattr(result, "tolist") else list(result)
            if len(vectors) != len(values) or any(
                len(vector) != self.dim or not all(math.isfinite(float(v)) for v in vector)
                for vector in vectors
            ):
                raise ValueError("Invalid local embedding result")
            return [[float(value) for value in vector] for vector in vectors]
        except Exception:
            raise RuntimeError("Local embedding generation failed; stored knowledge was preserved") from None


    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_texts(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embed_provider == "stub" and settings.test_mode:
        from backend.app.testing.embeddings import StubEmbeddingProvider

        return StubEmbeddingProvider()
    if settings.embed_provider == "sentence_transformers":
        return SentenceTransformerEmbeddingProvider(settings)
    raise ValueError("Live embeddings must use local SentenceTransformers")
