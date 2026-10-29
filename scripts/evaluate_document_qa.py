#!/usr/bin/env python3
"""Offline retrieval evaluation for one saved document (no network, no Azure).

Usage: .venv/bin/python scripts/evaluate_document_qa.py SPEC.json EXTRACTION.json

The spec and extraction may be private (keep them under store/local/); the
script reads them and indexes the extraction into a temporary directory only.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.testing.network_guard import install_offline_network_guard  # noqa: E402

install_offline_network_guard()

from backend.app.evaluation.document_eval import evaluate, render, temporary_settings  # noqa: E402


def main() -> int:
    spec = json.loads(Path(sys.argv[1]).read_text())
    extraction = json.loads(Path(sys.argv[2]).read_text())
    settings = temporary_settings()
    results = evaluate(spec, extraction, settings)
    print(render(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
