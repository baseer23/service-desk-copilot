"""Test helpers and fixtures for FastAPI client setup."""

from __future__ import annotations

import importlib
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.core import config as config_module

if "pdfminer.high_level" not in sys.modules:
    fake_pdfminer = types.ModuleType("pdfminer")
    fake_high_level = types.ModuleType("pdfminer.high_level")

    def _missing_extract_text(*_args, **_kwargs):  # pragma: no cover - shim for tests
        return ""

    fake_high_level.extract_text = _missing_extract_text
    fake_pdfminer.high_level = fake_high_level
    sys.modules.setdefault("pdfminer", fake_pdfminer)
    sys.modules.setdefault("pdfminer.high_level", fake_high_level)

if "chromadb" not in sys.modules:
    fake_chromadb = types.ModuleType("chromadb")
    fake_config = types.ModuleType("chromadb.config")

    class _FakeCollection:
        def __init__(self):
            self._records: Dict[str, Dict[str, Any]] = {}

        def upsert(self, ids, documents, metadatas, embeddings):
            for idx, chunk_id in enumerate(ids):
                self._records[chunk_id] = {
                    "id": chunk_id,
                    "text": documents[idx] if idx < len(documents) else "",
                    "metadata": metadatas[idx] if idx < len(metadatas) else {},
                    "embedding": embeddings[idx] if idx < len(embeddings) else [],
                }

        def query(self, query_embeddings, n_results):
            query = query_embeddings[0]

            def _dist(record):
                embedding = record.get("embedding") or []
                return sum((float(a) - float(b)) ** 2 for a, b in zip(query, embedding)) ** 0.5

            ordered = sorted(self._records.values(), key=_dist)
            top = ordered[:n_results]
            return {
                "ids": [[rec["id"] for rec in top]],
                "documents": [[rec.get("text", "") for rec in top]],
                "metadatas": [[rec.get("metadata", {}) for rec in top]],
                "distances": [[_dist(rec) for rec in top]],
            }

        def count(self):  # noqa: D401 - mimic Chroma API
            return len(self._records)

    class _FakePersistentClient:
        def __init__(self, *_, **__):  # pragma: no cover - deterministic stub
            self._collection = _FakeCollection()

        def get_or_create_collection(self, name):  # noqa: D401 - mimic API
            return self._collection

    class _FakeSettings:
        def __init__(self, **_kwargs):  # pragma: no cover - placeholder
            return

    fake_chromadb.PersistentClient = _FakePersistentClient  # type: ignore[attr-defined]
    fake_config.Settings = _FakeSettings  # type: ignore[attr-defined]

    sys.modules.setdefault("chromadb", fake_chromadb)
    sys.modules.setdefault("chromadb.config", fake_config)


@pytest.fixture
def make_client(monkeypatch) -> Callable[[Dict[str, Any]], TestClient]:
    """Return a factory that builds a TestClient with env overrides."""

    def factory(env: Dict[str, Any] | None = None) -> TestClient:
        overrides = dict(env or {})
        use_real_repos = bool(overrides.pop("__use_real_repos", False))
        for key, value in overrides.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, str(value))

        # ensure settings pick up the latest environment
        config_module.reload_settings()

        import backend.app.main as main
        importlib.reload(main)
        if not use_real_repos:
            from backend.app.store.graph_repo import InMemoryGraphRepository
            from backend.app.store.vector_chroma import InMemoryVectorStore

            main._init_graph_repo = lambda settings: (InMemoryGraphRepository(), None)  # type: ignore[attr-defined]
            main._init_vector_store = lambda settings: InMemoryVectorStore()  # type: ignore[attr-defined]
            main.app.state.vector_store = InMemoryVectorStore()
            main.app.state.graph_repo = InMemoryGraphRepository()
            main.app.state.graph_driver = None
        client = TestClient(main.app)
        return client

    return factory
