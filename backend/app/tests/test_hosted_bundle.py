"""Deployment integrity checks independent of simulated AI providers."""

import hashlib
import io
from zipfile import ZipFile

import pytest
from scripts.container_bootstrap import unpack
from scripts.package_azure import package, revision_suffix


def bundle(name, content=b"reviewed"):
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr(name, content)
    return stream.getvalue()


def test_verified_bundle_rejects_tampering_and_path_escape(tmp_path):
    content = bundle("backend/app/main.py")
    digest = hashlib.sha256(content).hexdigest()
    unpack(content, tmp_path, digest)
    assert (tmp_path / "backend/app/main.py").read_bytes() == b"reviewed"
    with pytest.raises(ValueError, match="integrity"):
        unpack(content + b"tampered", tmp_path, digest)
    for path in ["../outside.py", "/outside.py", "backend\\outside.py"]:
        invalid = bundle(path)
        with pytest.raises(ValueError, match="Unsafe"):
            unpack(invalid, tmp_path, hashlib.sha256(invalid).hexdigest())


def test_host_package_preserves_linux_libraries_and_excludes_private_data(tmp_path):
    root = tmp_path / "repo"
    for path in [
        "backend/app/main.py",
        "frontend/dist/index.html",
        "requirements-azure.txt",
        "backend/app/voice/prompts/support_v1.md",
    ]:
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("reviewed")
    for path in [".env", "backend/cases.db", "backend/private.json", "backend/tests/test_demo.py"]:
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("private")
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    (wheels / "example-py3-none-any.whl").write_bytes(bundle("example/__init__.py"))
    destination = tmp_path / "deploy.zip"
    package(root, destination, wheels)
    with ZipFile(destination) as archive:
        assert set(archive.namelist()) == {
            "backend/app/main.py",
            "frontend/dist/index.html",
            "requirements.txt",
            "backend/app/voice/prompts/support_v1.md",
            ".python_packages/lib/site-packages/example/__init__.py",
        }
        assert all(archive.read(name) != b"private" for name in archive.namelist())
    assert destination.stat().st_mode & 0o777 == 0o600


def test_actual_voice_prompt_is_preserved_in_host_bundle(tmp_path):
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    destination = tmp_path / "application.zip"
    package(root, destination)
    prompt = "backend/app/voice/prompts/support_v1.md"
    with ZipFile(destination) as archive:
        assert archive.read(prompt) == (root / prompt).read_bytes()


def test_configuration_and_image_changes_receive_distinct_immutable_revisions():
    first = {"image": "reviewed-v1", "command": "standard", "env": {"shutdown": True}}
    assert revision_suffix(first) == revision_suffix(dict(reversed(list(first.items()))))
    assert revision_suffix(first) != revision_suffix({**first, "image": "reviewed-v2"})
    assert revision_suffix(first) != revision_suffix({**first, "command": "protected-diagnostic"})
    assert revision_suffix(first) != revision_suffix({**first, "env": {"shutdown": False}})
