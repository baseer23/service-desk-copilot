"""Local transactional LangGraph checkpoints; tenant authority never comes from a thread ID."""

import asyncio
import base64
import json

from langgraph.checkpoint.base import WRITES_IDX_MAP, BaseCheckpointSaver, CheckpointTuple
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from backend.app.store.local_records import LocalRecords


class LocalCheckpointSaver(BaseCheckpointSaver):
    def __init__(self, records: LocalRecords):
        super().__init__(serde=JsonPlusSerializer(pickle_fallback=False))
        self.records = records

    @staticmethod
    def _key(config, checkpoint_id):
        value = config["configurable"]
        return json.dumps(
            [value["thread_id"], value.get("checkpoint_ns", ""), checkpoint_id],
            separators=(",", ":"),
        )

    def _dump(self, value):
        kind, data = self.serde.dumps_typed(value)
        return {"type": kind, "data": base64.b64encode(data).decode()}

    def _load(self, value):
        if value["type"] == "pickle":
            raise ValueError("Pickle checkpoints are not accepted")
        return self.serde.loads_typed(
            (value["type"], base64.b64decode(value["data"], validate=True))
        )

    async def aget_tuple(self, config):
        values = config["configurable"]
        checkpoint_id = values.get("checkpoint_id")
        if checkpoint_id:
            row = await asyncio.to_thread(
                self.records.read, "checkpoint", self._key(config, checkpoint_id)
            )
            if row is None:
                legacy = (
                    values["thread_id"]
                    + ":"
                    + values.get("checkpoint_ns", "")
                    + ":"
                    + checkpoint_id
                )
                row = await asyncio.to_thread(self.records.read, "checkpoint", legacy)
            if row and (
                row.get("thread_id") != values["thread_id"]
                or row.get("namespace") != values.get("checkpoint_ns", "")
                or row.get("checkpoint_id") != checkpoint_id
            ):
                row = None
        else:
            rows = await asyncio.to_thread(
                self.records.query,
                "checkpoint",
                fields={
                    "thread_id": values["thread_id"],
                    "namespace": values.get("checkpoint_ns", ""),
                },
            )
            row = max(rows, key=lambda row: row["checkpoint_id"]) if rows else None
        if not row:
            return None
        saved = row["payload"]
        actual = {
            "configurable": {
                "thread_id": values["thread_id"],
                "checkpoint_ns": values.get("checkpoint_ns", ""),
                "checkpoint_id": row["checkpoint_id"],
            }
        }
        writes = await asyncio.to_thread(
            self.records.query,
            "checkpoint_write",
            fields={
                "thread_id": values["thread_id"],
                "namespace": values.get("checkpoint_ns", ""),
                "checkpoint_id": row["checkpoint_id"],
            },
        )
        pending = {}
        for row in writes:
            for entry in row["payload"].get("writes", [row["payload"]]):
                # Queries return newest records first. Mixed legacy/new task
                # records must not replay the same channel twice after restart.
                pending.setdefault((entry["task_id"], entry["index"]), entry)
        return CheckpointTuple(
            actual,
            self._load(saved["checkpoint"]),
            self._load(saved["metadata"]),
            saved.get("parent_config"),
            [
                (
                    item["task_id"],
                    item["channel"],
                    self._load(item["value"]),
                )
                for item in sorted(
                    pending.values(),
                    key=lambda entry: entry["index"],
                )
            ],
        )

    async def aput(self, config, checkpoint, metadata, new_versions):
        values = config["configurable"]
        fields = {
            "thread_id": values["thread_id"],
            "namespace": values.get("checkpoint_ns", ""),
            "checkpoint_id": checkpoint["id"],
        }
        payload = {
            "checkpoint": self._dump(checkpoint),
            "metadata": self._dump(metadata),
            "parent_config": {
                "configurable": {
                    key: values[key]
                    for key in ("thread_id", "checkpoint_ns", "checkpoint_id")
                    if key in values
                }
            }
            if values.get("checkpoint_id")
            else None,
        }
        await asyncio.to_thread(
            self.records.write,
            "checkpoint",
            self._key(config, checkpoint["id"]),
            payload,
            fields=fields,
        )
        return {"configurable": {**values, "checkpoint_id": checkpoint["id"]}}

    async def aput_writes(self, config, writes, task_id, task_path=""):
        values = config["configurable"]
        fields = {
            "thread_id": values["thread_id"],
            "namespace": values.get("checkpoint_ns", ""),
            "checkpoint_id": values["checkpoint_id"],
        }
        # One conditional record per task, rather than a quota lease and document
        # write for every state channel. Existing per-channel records remain readable.
        key = self._key(config, values["checkpoint_id"]) + ":" + task_id + ":batch"
        previous = await asyncio.to_thread(self.records.read, "checkpoint_write", key)
        saved = {
            entry["index"]: entry
            for entry in previous["payload"]["writes"]
        } if previous else {}
        changed = False
        for index, (channel, value) in enumerate(writes):
            position = WRITES_IDX_MAP.get(channel, index)
            if position >= 0 and position in saved:
                continue
            saved[position] = {
                "task_id": task_id, "task_path": task_path, "index": position,
                "channel": channel, "value": self._dump(value),
            }
            changed = True
        if changed:
            await asyncio.to_thread(
                self.records.write, "checkpoint_write", key,
                {"writes": list(saved.values())}, fields=fields,
                previous=previous, create_only=previous is None,
            )

    async def alist(self, config, *, filter=None, before=None, limit=None):  # noqa: A002 - LangGraph override uses the canonical API parameter.
        if config is None:
            raise ValueError("A tenant-scoped thread configuration is required")
        values = config["configurable"]
        rows = await asyncio.to_thread(
            self.records.query,
            "checkpoint",
            fields={"thread_id": values["thread_id"], "namespace": values.get("checkpoint_ns", "")},
        )
        emitted = 0
        for row in sorted(rows, key=lambda row: row["checkpoint_id"], reverse=True):
            if before and row["checkpoint_id"] >= before["configurable"]["checkpoint_id"]:
                continue
            if filter and any(
                self._load(row["payload"]["metadata"]).get(key) != value
                for key, value in filter.items()
            ):
                continue
            result = await self.aget_tuple(
                {"configurable": {**values, "checkpoint_id": row["checkpoint_id"]}}
            )
            if result:
                yield result
                emitted += 1
                if limit is not None and emitted >= limit:
                    break

    def get_next_version(self, current, channel):
        if isinstance(current, str):
            return f"{int(current.split('.')[0]) + 1:032d}"
        return int(current or 0) + 1
