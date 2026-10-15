"""Reindex preserved text using local embeddings. Defaults to a secret-free dry run."""

import argparse
import json
import os
from pathlib import Path

from backend.app.core.config import Settings
from backend.app.services.local_reembedding import migrate_local_embeddings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Add the local model collection; never delete original data")
    parser.add_argument("--enrich-graph", action="store_true", help="Publish actual document/entity links to configured Aura")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    result = migrate_local_embeddings(Settings(), apply=args.apply, enrich_graph=args.enrich_graph)
    text = json.dumps(result, indent=2)
    if args.report:
        descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as target:
            target.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
