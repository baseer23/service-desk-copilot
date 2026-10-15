"""Offline local cutover preparation; requires an intact, verified archived export."""

import argparse
import json
from pathlib import Path

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from backend.app.core.config import Settings
    from backend.app.services.local_migration import migrate

    output = args.output.resolve()
    settings = Settings().model_copy(
        update={
            "data_platform": "local",
            "local_persistence_enabled": True,
            "case_db_path": output / "state.sqlite3",
            "chroma_dir": output / "chroma",
            "local_documents_dir": output / "documents",
        }
    )
    print(json.dumps(migrate(args.backup, settings)))
