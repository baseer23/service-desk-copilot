"""Local embeddings cannot dispatch paid Azure inference or mix model spaces."""

from types import SimpleNamespace

import pytest
from backend.app.adapters.embeddings import SentenceTransformerEmbeddingProvider, get_embedding_provider
from backend.app.testing.embeddings import StubEmbeddingProvider


def test_stub_embeddings_are_deterministic():
    provider = StubEmbeddingProvider(dim=384)
    first = provider.embed_texts(["alpha", "beta"])
    assert first == provider.embed_texts(["alpha", "beta"])
    assert len(first) == 2 and len(first[0]) == 384


def test_local_embedding_provider_checks_model_provenance_and_outputs(settings):
    config = settings.model_copy(update={"embed_provider": "sentence_transformers", "embed_model_name": "all-MiniLM-L6-v2"})
    calls = []
    model = SimpleNamespace(encode=lambda values, **kwargs: calls.append((values, kwargs)) or [[1.0] + [0.0] * 383 for value in values])
    provider = SentenceTransformerEmbeddingProvider(config, model=model)
    assert provider.embed_texts(["one", "two"])[0][0] == 1
    assert calls[0][1]["normalize_embeddings"] is True
    assert provider.provider == "sentence_transformers" and provider.dim == 384
    assert len(provider.embed_query("LangChain query")) == 384
    assert len(provider.embed_documents(["LangChain document"])[0]) == 384
    assert provider.embed_texts([]) == []
    with pytest.raises(ValueError, match="text"):
        provider.embed_texts([42])
    with pytest.raises(ValueError, match="all-MiniLM"):
        SentenceTransformerEmbeddingProvider(config.model_copy(update={"embed_model_name": "text-embedding-3-small"}))


@pytest.mark.parametrize("vector", [[0.0] * 383, [float("nan")] * 384, [float("inf")] * 384])
def test_local_model_invalid_vectors_fail_without_source_text_leak(settings, vector):
    config = settings.model_copy(update={"embed_provider": "sentence_transformers", "embed_model_name": "all-MiniLM-L6-v2"})
    provider = SentenceTransformerEmbeddingProvider(config, model=SimpleNamespace(encode=lambda *args, **kwargs: [vector]))
    with pytest.raises(RuntimeError, match="Local embedding generation failed") as caught:
        provider.embed_texts(["private source text"])
    assert "private source text" not in str(caught.value)


def test_factory_never_uses_azure_embeddings_or_silently_stubs(settings):
    config = settings.model_copy(update={"embed_provider": "sentence_transformers", "embed_model_name": "all-MiniLM-L6-v2", "test_mode": False})
    assert isinstance(get_embedding_provider(config), SentenceTransformerEmbeddingProvider)
    for provider in ["azure_openai", "stub"]:
        with pytest.raises(ValueError):
            get_embedding_provider(config.model_copy(update={"embed_provider": provider}))
