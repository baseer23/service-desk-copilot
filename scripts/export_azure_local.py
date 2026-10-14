"""Read-only, complete Azure data export. No model calls, keys or source deletion."""

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path


def digest_file(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def export(settings, output):
    from azure.core import MatchConditions
    from azure.search.documents import SearchClient
    from azure.storage.blob import BlobServiceClient
    from backend.app.services.azure_migration import snapshot_sources
    from backend.app.store.azure_records import CosmosRecords, azure_credential

    os.umask(0o077)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    manifest = {"format": 1, "files": {}, "counts": {}, "source_mutations": False}

    def save_rows(name, rows):
        path = output / name
        with path.open("w") as target:
            for row in rows:
                target.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        manifest["files"][name] = {"sha256": digest_file(path), "bytes": path.stat().st_size}

    records = CosmosRecords(settings)
    credential = azure_credential(settings)
    try:
        rows = list(
            records.container.query_items(
                query="SELECT * FROM c",
                enable_cross_partition_query=True,
                max_item_count=100,
            )
        )
        rows.sort(key=lambda row: (row["tenant_id"], row["id"]))
        save_rows("records.jsonl", rows)
        manifest["counts"]["records"] = len(rows)
        manifest["counts"]["record_kinds"] = dict(Counter(row["kind"] for row in rows))
        with SearchClient(
            settings.azure_search_endpoint, settings.azure_search_index, credential, retry_total=0
        ) as search:
            found = search.search(search_text="*", include_total_count=True)
            expected = found.get_count()
            chunks = list(found)
            if len(chunks) != expected:
                raise RuntimeError("Search export count mismatch")
            save_rows("search.jsonl", sorted(chunks, key=lambda row: row["id"]))
            manifest["counts"]["search_chunks"] = len(chunks)
            manifest["counts"]["retrievable_vectors"] = sum(bool(c.get("vector")) for c in chunks)
        with BlobServiceClient(
            settings.azure_storage_account_url, credential, retry_total=0
        ) as service:
            container = service.get_container_client(settings.azure_storage_container)
            blobs = []
            for item in container.list_blobs(include=["metadata"]):
                relative = Path(item.name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise RuntimeError("Unsafe artifact path")
                path = output / "blobs" / relative
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                stream = container.get_blob_client(item.name).download_blob(
                    etag=item.etag,
                    match_condition=MatchConditions.IfNotModified,
                    max_concurrency=1,
                )
                with path.open("wb") as target:
                    stream.readinto(target)
                digest = digest_file(path)
                if path.stat().st_size != item.size or (
                    item.metadata.get("sha256") and item.metadata["sha256"] != digest
                ):
                    raise RuntimeError("Blob backup integrity mismatch")
                entry = {"name": item.name, "bytes": item.size, "sha256": digest}
                blobs.append(entry)
                manifest["files"]["blobs/" + item.name] = {"bytes": item.size, "sha256": digest}
            manifest["counts"]["blobs"] = len(blobs)
            manifest["counts"]["blob_bytes"] = sum(item["bytes"] for item in blobs)
            save_rows("blobs.jsonl", blobs)
        snapshot_sources(settings, output / "previous-local")
        for file in (output / "previous-local").rglob("*"):
            if file.is_file():
                manifest["files"][str(file.relative_to(output))] = {
                    "sha256": digest_file(file),
                    "bytes": file.stat().st_size,
                }
        # A second complete metadata pass detects writes during the snapshot.
        after = list(
            records.container.query_items(
                query="SELECT c.id,c.tenant_id,c._etag FROM c",
                enable_cross_partition_query=True,
            )
        )

        def signature(values):
            return sorted((r["tenant_id"], r["id"], r["_etag"]) for r in values)

        manifest["stable_records"] = signature(rows) == signature(after)
        for name, entry in manifest["files"].items():
            if digest_file(output / name) != entry["sha256"]:
                raise RuntimeError("Backup reread integrity mismatch")
        manifest["verified"] = True
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
        print(
            json.dumps(
                {
                    "backup": str(output),
                    "counts": manifest["counts"],
                    "verified": True,
                    "stable_records": manifest["stable_records"],
                }
            )
        )
        return manifest
    finally:
        records.close()
        credential.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from backend.app.core.config import Settings

    export(Settings(), args.output.resolve())
