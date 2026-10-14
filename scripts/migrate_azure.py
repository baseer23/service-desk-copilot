"""Back up local stores; migrate only explicitly approved fictional test data.

The current .env and local stores are never overwritten or deleted. Existing
customer stores require a separate authorized input path; dry-run is the default.
"""

import argparse
import asyncio
import json
import os
import sqlite3
import uuid
from pathlib import Path


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--configuration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply-approved-fictional-data", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    from backend.app.core.config import get_settings
    from backend.app.core.principal import Principal, principal_scope
    from backend.app.services.azure_migration import RecordMigration, snapshot_sources
    from backend.app.store.azure_records import CosmosRecords

    local = get_settings()
    snapshot_sources(local, args.output)
    if not args.apply_approved_fictional_data:
        print(json.dumps({"backups_verified": True, "cloud_write": False}))
        return
    # Deliberate restriction: this known isolation root was created using fictional
    # fixtures. Never infer permission to upload the user's earlier store/ directory.
    expected = Path("/private/tmp/deskmate-azure-activation-20261013-nn8sspiz")
    for path in [local.case_db_path, local.chroma_dir, local.azure_usage_db_path]:
        if not path or not path.resolve().is_relative_to(expected):
            raise ValueError(
                "Only the previously approved isolated fictional stores can be applied"
            )
    config = json.loads((args.configuration / "host-settings.private.json").read_text())
    cloud = local.model_copy(
        update={
            "data_platform": "azure",
            "azure_cosmos_endpoint": config["AZURE_COSMOS_ENDPOINT"],
            "azure_cosmos_database": "deskmate",
            "azure_cosmos_container": "state",
            "azure_storage_account_url": config["AZURE_STORAGE_ACCOUNT_URL"],
            "azure_search_endpoint": config["AZURE_SEARCH_ENDPOINT"],
            "azure_document_endpoint": config["AZURE_DOCUMENT_ENDPOINT"],
            "azure_search_semantic_enabled": True,
            "auth_allowed_tenants": [config["AUTH_ALLOWED_TENANTS"]],
        }
    )
    records = CosmosRecords(cloud)
    migration = RecordMigration(records, uuid.uuid4().hex)
    copied = {
        "cases": 0,
        "facts": 0,
        "events": 0,
        "documents": 0,
        "vectors": 0,
        "checkpoints": 0,
        "voice": 0,
    }
    try:
        with principal_scope(
            Principal(config["AUTH_ALLOWED_TENANTS"], config["AUTH_OPERATOR_IDS"], True)
        ):
            source = args.output / local.case_db_path.name
            with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as db:
                db.row_factory = sqlite3.Row
                tables = {
                    row[0]
                    for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                for row in db.execute("SELECT * FROM cases"):
                    copied["cases"] += migration.copy(
                        "case", row["case_id"], json.loads(row["state_json"])
                    )
                for row in db.execute("SELECT * FROM facts"):
                    payload = dict(row)
                    payload["metadata"] = json.loads(payload.pop("metadata_json"))
                    copied["facts"] += migration.copy(
                        "fact",
                        "legacy-" + str(row["id"]),
                        payload,
                        {"case_id": row["case_id"], "subject": row["subject"]},
                    )
                for row in db.execute("SELECT * FROM events"):
                    payload = {
                        key: row[key] for key in ("id", "tool", "source", "content", "created_at")
                    }
                    copied["events"] += migration.copy(
                        "event",
                        row["case_id"] + ":legacy-" + str(row["id"]),
                        payload,
                        {"case_id": row["case_id"]},
                    )
                if "voice_sessions" in tables:
                    for row in db.execute("SELECT * FROM voice_sessions"):
                        state = json.loads(row["state_json"])
                        if state["confirmed_profile"] or state["turns"]:
                            raise ValueError(
                                "Voice transcripts need explicit owner mapping "
                                "and data authorization"
                            )
                        state["tenant_id"] = config["AUTH_ALLOWED_TENANTS"]
                        state["owner_id"] = config["AUTH_OPERATOR_IDS"]
                        payload = {"state": state, "lease_id": "", "lease_until": 0, "events": []}
                        copied["voice"] += migration.copy(
                            "voice", row["session_id"], payload, {"owner_id": state["owner_id"]}
                        )
                if "knowledge_documents" in tables:
                    from backend.app.services.azure_documents import AzureDocumentRepository

                    documents = AzureDocumentRepository(cloud, records=records)
                    for row in db.execute("SELECT * FROM knowledge_documents"):
                        result = documents.upload(row["text"].encode(), row["title"], "text/plain")
                        copied["documents"] += not result["duplicate"]
            from backend.app.core.azure_managed_usage import TABLES

            with sqlite3.connect(
                (args.output / local.azure_usage_db_path.name).as_uri() + "?mode=ro", uri=True
            ) as db:
                db.row_factory = sqlite3.Row
                ledger = {
                    name: [dict(row) for row in db.execute(f"SELECT * FROM {name}")]  # noqa: S608 - Identifiers come from a fixed validated allowlist.
                    for name in TABLES
                }
            if not any(row["disabled"] for row in ledger["azure_policy"]):
                raise ValueError("The original persistent emergency shutdown must remain enabled")
            migration.copy(
                "usage_policy", "subscription", {"tables": ledger}, tenant="system:usage"
            )
            import chromadb
            from backend.app.store.azure_knowledge import AzureSearchStore
            from chromadb.config import Settings as ChromaSettings

            chroma = chromadb.PersistentClient(
                path=str(args.output / "chroma"),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            search = AzureSearchStore(cloud, records=records)
            for collection in chroma.list_collections():
                metadata = collection.metadata or {}
                if (
                    metadata.get("embedding_provider") != "azure_openai"
                    or metadata.get("embedding_model") != cloud.embed_model_name
                ):
                    continue  # Unknown/test vectors remain in the verified backup.
                values = collection.get(include=["documents", "metadatas", "embeddings"])
                chunks = [
                    {"id": identifier, "text": text, "metadata": meta, "embedding": list(vector)}
                    for identifier, text, meta, vector in zip(
                        values["ids"],
                        values["documents"],
                        values["metadatas"],
                        values["embeddings"],
                        strict=True,
                    )
                ]
                if any(
                    len(chunk["embedding"]) != cloud.azure_embedding_dimensions
                    or chunk["metadata"].get("embedding_provider") != "azure_openai"
                    or chunk["metadata"].get("embedding_model") != cloud.embed_model_name
                    for chunk in chunks
                ):
                    raise ValueError(
                        "Vector dimensions or provenance do not match the managed index"
                    )
                search.upsert(chunks)
                copied["vectors"] += len(chunks)
            checkpoint = args.output / (local.case_db_path.name + ".supervisor.checkpoints.sqlite3")
            if checkpoint.exists():
                from backend.app.agent.azure_checkpoints import CosmosCheckpointSaver
                from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

                saver = CosmosCheckpointSaver(records)
                async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as original:
                    async for item in original.alist(None):
                        await saver.aput(
                            item.parent_config
                            or {
                                "configurable": {
                                    key: value
                                    for key, value in item.config["configurable"].items()
                                    if key != "checkpoint_id"
                                }
                            },
                            item.checkpoint,
                            item.metadata,
                            {},
                        )
                        pending = {}
                        for task, channel, value in item.pending_writes or []:
                            pending.setdefault(task, []).append((channel, value))
                        for task, writes in pending.items():
                            await saver.aput_writes(item.config, writes, task)
                        reread = await saver.aget_tuple(item.config)
                        if reread.checkpoint != item.checkpoint:
                            raise RuntimeError("Checkpoint migration integrity failed")
                        copied["checkpoints"] += 1
            (args.output / "migration-manifest.private.json").write_text(
                json.dumps(
                    {
                        "migration_id": migration.migration_id,
                        "records": migration.manifest,
                        "counts": copied,
                    },
                    indent=2,
                )
            )
            print(
                json.dumps(
                    {
                        "backups_verified": True,
                        "managed_records_verified": True,
                        "counts": copied,
                        "emergency_shutdown_preserved": True,
                    }
                )
            )
    finally:
        # Partial migrations remain reviewable; reruns compare payload hashes.
        (args.output / "migration-manifest.private.json").write_text(
            json.dumps(
                {
                    "migration_id": migration.migration_id,
                    "records": migration.manifest,
                    "counts": copied,
                },
                indent=2,
            )
        )
        records.close()


if __name__ == "__main__":
    asyncio.run(main())
