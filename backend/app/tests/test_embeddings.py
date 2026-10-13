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


def test_document_embeddings_batch_more_than_four_chunks_with_usage_accounting(settings):
    from types import SimpleNamespace

    from backend.app.adapters.embeddings import AzureEmbeddingProvider

    config = settings.model_copy(
        update={"azure_embedding_deployment": "test", "azure_usage_guard_enabled": True}
    )
    calls = []

    def create(**kwargs):
        values = kwargs["input"]
        calls.append(values)
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=i, embedding=[float(value)] * 384)
                for i, value in reversed(list(enumerate(values)))
            ],
            usage={"prompt_tokens": len(values), "total_tokens": len(values)},
        )

    provider = AzureEmbeddingProvider(
        config, SimpleNamespace(embeddings=SimpleNamespace(create=create))
    )
    vectors = provider.embed_texts([str(value) for value in range(9)])
    assert [len(batch) for batch in calls] == [4, 4, 1]
    assert [vector[0] for vector in vectors] == list(range(9))
    assert provider.usage_guard.snapshot()["reported_tokens"]["input_tokens"] == 9


def test_embedding_pacing_deadline_prevents_dispatch_and_emergency_stops_later_batches(
    settings, monkeypatch
):
    from types import SimpleNamespace

    import pytest
    from backend.app.adapters.embeddings import AzureEmbeddingProvider
    from backend.app.core.azure_usage import AzureUsageBlocked

    config = settings.model_copy(
        update={
            "azure_embedding_deployment": "test",
            "azure_usage_guard_enabled": True,
            "model_timeout_sec": 1,
        }
    )
    calls = []
    provider = None

    def create(**kwargs):
        calls.append(kwargs)
        provider.usage_guard.disable()
        return SimpleNamespace(
            data=[SimpleNamespace(index=i, embedding=[0.1] * 384) for i in range(4)],
            usage={"prompt_tokens": 4, "total_tokens": 4},
        )

    provider = AzureEmbeddingProvider(
        config, SimpleNamespace(embeddings=SimpleNamespace(create=create))
    )
    with monkeypatch.context() as patch:
        patch.setattr(provider.usage_guard, "request_delay", lambda kind: 10)
        with pytest.raises(RuntimeError, match="deadline"):
            provider.embed_texts(["one"])
        assert calls == []
    with pytest.raises(AzureUsageBlocked, match="disabled"):
        provider.embed_texts(["one"] * 5)
    assert len(calls) == 1 and not provider.usage_guard.snapshot()["enabled"]
