"""Lifespan-aware clients using installed packages and isolated persistent stores."""

import os
import sys
from contextlib import ExitStack
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.testing.network_guard import install_offline_network_guard

# Collection imports the application before fixtures run. Offline is the
# fail-closed default even if a developer's .env contains real credentials.
TEST_PROFILE = os.getenv("DESKMATE_TEST_PROFILE", "offline")
if TEST_PROFILE == "live":
    if os.getenv("DESKMATE_ALLOW_LIVE_API_TESTS", "false").casefold() != "true":
        raise RuntimeError("Live tests require DESKMATE_ALLOW_LIVE_API_TESTS=true")
elif TEST_PROFILE == "offline":
    for key, value in {
        "DESKMATE_TEST_PROFILE": "offline",
        "DESKMATE_ALLOW_LIVE_API_TESTS": "false",
        "TEST_MODE": "true",
        "DATA_PLATFORM": "local",
        "LOCAL_PERSISTENCE_ENABLED": "false",
        "DEPLOYMENT_MODE": "local",
        "ENABLE_LOCAL_FIXTURES": "true",
        "GENERATION_PROVIDER": "stub",
        "EMBED_PROVIDER": "stub",
        "SPEECH_ENABLED": "false",
        "NEO4J_URI": "",
        "MCP_SERVERS": "[]",
    }.items():
        os.environ[key] = value
else:
    raise RuntimeError("DESKMATE_TEST_PROFILE must be 'offline' or 'live'")

if TEST_PROFILE == "offline":
    install_offline_network_guard()


@pytest.fixture
def settings(tmp_path):
    from backend.app.core.config import Settings

    return Settings(
        _env_file=None,
        test_mode=True,
        generation_provider="stub",
        embed_provider="stub",
        neo4j_uri="",
        chroma_dir=tmp_path / "chroma",
        case_db_path=tmp_path / "cases.db",
        url_rate_limit_sec=0,
    )


@pytest.fixture
def make_client(monkeypatch, tmp_path):
    with ExitStack() as stack:

        def factory(env=None):
            defaults = {
                "TEST_MODE": "true",
                "GENERATION_PROVIDER": "stub",
                "EMBED_PROVIDER": "stub",
                "NEO4J_URI": "",
                "CHROMA_DIR": str(tmp_path / "chroma"),
                "CASE_DB_PATH": str(tmp_path / "cases.db"),
                "LOCAL_DOCUMENTS_DIR": str(tmp_path / "documents"),
            }
            defaults.update({k: v for k, v in (env or {}).items() if not k.startswith("__")})
            for key, value in defaults.items():
                if value is None:
                    monkeypatch.delenv(key, raising=False)
                else:
                    monkeypatch.setenv(key, str(value))
            from backend.app.core.config import Settings
            from backend.app.main import create_app
            import backend.app.main as main

            if (env or {}).get("__GRAPH_WRITER_AVAILABLE"):
                import backend.app.services.local_documents as local_documents

                monkeypatch.setattr(
                    local_documents,
                    "graph_writer_readiness",
                    lambda _settings: {"available": True, "blocker": None},
                )

            app = create_app(Settings(_env_file=None))
            monkeypatch.setattr(main, "app", app)
            return stack.enter_context(TestClient(app))

        yield factory
