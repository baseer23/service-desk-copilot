"""Select the durable checkpoint implementation without resetting local history."""

from contextlib import asynccontextmanager

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


@asynccontextmanager
async def checkpoint_store(settings):
    if settings.uses_record_store:
        from backend.app.agent.azure_checkpoints import CosmosCheckpointSaver
        from backend.app.store.local_records import record_store

        records = record_store(settings)
        try:
            yield CosmosCheckpointSaver(records)
        finally:
            records.close()
    else:
        settings.case_db_path.parent.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(
            str(settings.case_db_path) + ".supervisor.checkpoints.sqlite3"
        ) as saver:
            await saver.setup()
            yield saver
