import math

from backend.app.testing.embeddings import StubEmbeddingProvider


def test_stub_embeddings_are_deterministic():
    provider = StubEmbeddingProvider(dim=384)
    first = provider.embed_texts(["alpha", "beta"])
    second = provider.embed_texts(["alpha", "beta"])
    assert first == second
    assert len(first) == 2
    assert len(first[0]) == 384
    assert math.isclose(sum(first[0]), sum(second[0]))


def test_azure_embeddings_validate_order_dimensions_and_errors(settings):
    from types import SimpleNamespace

    import pytest
    from backend.app.adapters.embeddings import AzureEmbeddingProvider

    s = settings.model_copy(
        update={
            "azure_embedding_deployment": "embedding",
            "azure_embedding_dimensions": 384,
            "azure_usage_guard_enabled": True,
        }
    )
    result = SimpleNamespace(
        data=[
            SimpleNamespace(index=1, embedding=[0.2] * 384),
            SimpleNamespace(index=0, embedding=[0.1] * 384),
        ]
    )
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return result

    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    provider = AzureEmbeddingProvider(s, client=client)
    assert provider.embed_texts(["one", "two"])[0][0] == 0.1
    assert captured["dimensions"] == 384 and captured["model"] == "embedding"
    result.data[0].embedding = [float("nan")] * 384
    with pytest.raises(RuntimeError, match="embedding"):
        provider.embed_texts(["one", "two"])
