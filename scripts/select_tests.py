#!/usr/bin/env python3
"""Select and optionally run conservative offline tests for changed paths."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BACKEND_MAP = {
    "services/semantic_memory.py": ["test_semantic_memory.py", "test_graph_repo.py"],
    "services/conversation_history.py": ["test_general_conversations.py"],
    "agent/graph.py": [
        "test_supervisor.py", "test_general_conversations.py", "test_semantic_memory.py",
    ],
    "agent/checkpoints_records.py": ["test_local_persistence.py", "test_general_conversations.py"],
    "store/local_records.py": [
        "test_local_persistence.py", "test_semantic_memory.py", "test_storage_safety.py",
    ],
    "store/vector_chroma.py": [
        "test_vector_store.py", "test_semantic_memory.py", "test_retrieve.py",
    ],
    "store/graph_repo.py": [
        "test_graph_repo.py", "test_semantic_memory.py", "test_mcp_security.py",
    ],
    "mcp/server.py": ["test_mcp.py", "test_mcp_security.py", "test_semantic_memory.py"],
    "mcp/multi.py": ["test_multi_mcp.py", "test_mcp_security.py"],
    "main.py": ["test_cases.py", "test_general_conversations.py", "test_semantic_memory.py"],
    "models/provider": [
        "test_provider.py", "test_provider_selection.py", "test_provider_azure_openai.py",
    ],
    "services/url_crawler.py": ["test_ingest_url.py", "test_scope_source_policy.py"],
    "services/websites.py": [
        "test_websites.py", "test_ingest_url.py", "test_scope_source_policy.py",
    ],
    "services/ingest_service.py": ["test_ingest_service.py", "test_ingest_url.py"],
    "speech/": ["test_speech_stt.py"],
    "voice/": ["test_local_managed_voice_owner.py"],
}

FRONTEND_MAP = {
    "App.tsx": ["src/App.test.tsx"],
    "InvestigationHistory.tsx": ["src/InvestigationHistory.test.tsx"],
    "KnowledgePanel.tsx": ["src/KnowledgePanel.test.tsx"],
    "ProviderSelector.tsx": ["src/ProviderSelector.test.tsx", "src/api.test.ts"],
    "VoiceSupport.tsx": ["src/VoiceSupport.test.tsx", "src/voiceAudio.test.ts"],
    "api.ts": ["src/api.test.ts", "src/App.test.tsx"],
}


def git_output(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, check=False, capture_output=True, text=True
    )
    return [line for line in result.stdout.splitlines() if line]


def base_ref() -> str:
    for candidate in ("origin/dev", "dev"):
        if subprocess.run(
            ["git", "rev-parse", "--verify", candidate], cwd=ROOT,
            check=False, capture_output=True,
        ).returncode == 0:
            merge = git_output("merge-base", "HEAD", candidate)
            if merge:
                return merge[0]
    parent = git_output("rev-parse", "HEAD^")
    return parent[0] if parent else "HEAD"


def changed_paths(base: str | None = None) -> list[str]:
    reference = base or base_ref()
    paths = set(git_output("diff", "--name-only", f"{reference}...HEAD"))
    paths.update(git_output("diff", "--name-only"))
    paths.update(git_output("diff", "--name-only", "--cached"))
    paths.update(git_output("ls-files", "--others", "--exclude-standard"))
    return sorted(paths)


def select(paths: list[str], *, all_tests: bool = False) -> dict:
    if all_tests:
        return {
            "backend": ["backend/app/tests"], "frontend": ["src"],
            "python": [], "new_python": [], "typecheck": True, "e2e": True,
        }
    backend: set[str] = set()
    frontend: set[str] = set()
    python = {path for path in paths if path.endswith(".py")}
    tracked = set(git_output("ls-files"))
    new_python = python - tracked
    typecheck = False
    e2e = False
    unknown_code = False
    for path in paths:
        if path.startswith("backend/app/tests/") and path.endswith(".py"):
            backend.add(path)
        elif path.startswith("backend/app/") and path.endswith(".py"):
            relative = path.removeprefix("backend/app/")
            matched = False
            for key, tests in BACKEND_MAP.items():
                if key in relative:
                    backend.update("backend/app/tests/" + test for test in tests)
                    matched = True
            unknown_code |= not matched
        elif path.startswith("frontend/src/"):
            typecheck = True
            matched = False
            for key, tests in FRONTEND_MAP.items():
                if path.endswith(key) or key in path:
                    frontend.update(tests)
                    matched = True
            if not matched:
                frontend.add("src")
        elif path.startswith("frontend/tests/") or path == "frontend/playwright.config.ts":
            e2e = True
        elif path in {
            "Makefile", "pyproject.toml", "sitecustomize.py",
            "backend/app/tests/conftest.py", ".github/workflows/ci.yml",
        } or path.startswith(("scripts/select_tests", "backend/app/testing/network_guard")):
            backend.add("backend/app/tests/test_test_selector.py")
            backend.add("backend/app/tests/test_offline_network.py")
        elif path in {"requirements.txt", "frontend/package.json", "frontend/package-lock.json"}:
            unknown_code = True
    if unknown_code:
        backend.add("backend/app/tests")
    return {
        "backend": sorted(backend),
        "frontend": sorted(frontend),
        "python": sorted(python),
        "new_python": sorted(new_python),
        "typecheck": typecheck,
        "e2e": e2e,
    }


def run(selection: dict) -> int:
    commands: list[list[str]] = []
    if selection["python"]:
        commands.append([
            str(ROOT / ".venv/bin/python"), "-m", "ruff", "check",
            "--select", "E9,F63,F7,F82", *selection["python"],
        ])
    if selection["new_python"]:
        commands.append([
            str(ROOT / ".venv/bin/python"), "-m", "ruff", "check",
            *selection["new_python"],
        ])
    if selection["backend"]:
        commands.append([
            str(ROOT / ".venv/bin/python"), "-m", "pytest", "-q", "-m", "not live",
            *selection["backend"],
        ])
    if selection["frontend"]:
        commands.append([
            "npm", "--prefix", "frontend", "test", "--", "--run", *selection["frontend"],
        ])
    if selection["typecheck"]:
        commands.append(["npm", "--prefix", "frontend", "run", "typecheck"])
    if selection["e2e"]:
        commands.append(["npm", "--prefix", "frontend", "run", "test:e2e"])
    if not commands:
        print("No executable code changes detected; no tests selected.")
        return 0
    for command in commands:
        print("+", " ".join(command), flush=True)
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            return result.returncode
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    result = select(changed_paths(args.base), all_tests=args.all)
    if args.run:
        return run(result)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
