"""Lifespan-aware clients using installed packages and isolated persistent stores."""

import sys
import os
from contextlib import ExitStack
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

# Collection imports the application before fixtures run. Explicit isolated test mode
# prevents local .env settings from selecting any live generation/speech/embeddings.
for key, value in {
    "TEST_MODE": "true",
    "DATA_PLATFORM": "local",
    "LOCAL_PERSISTENCE_ENABLED": "false",
    "DEPLOYMENT_MODE": "local",
    "ENABLE_LOCAL_FIXTURES": "true",
    "GENERATION_PROVIDER": "stub",
    "EMBED_PROVIDER": "stub",
    "SPEECH_ENABLED": "false",
    "NEO4J_URI": "",
}.items():
    os.environ[key] = value

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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

            app = create_app(Settings(_env_file=None))
            monkeypatch.setattr(main, "app", app)
            return stack.enter_context(TestClient(app))

        yield factory
