import math

from backend.app.adapters.embeddings import StubEmbeddingProvider


def test_stub_embeddings_are_deterministic():
    provider = StubEmbeddingProvider(dim=384)
    first = provider.embed_texts(["alpha", "beta"])
    second = provider.embed_texts(["alpha", "beta"])
    assert first == second
    assert len(first) == 2
    assert len(first[0]) == 384
    assert math.isclose(sum(first[0]), sum(second[0]))
