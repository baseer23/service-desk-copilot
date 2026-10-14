"""Case facts/events stored as tenant-partitioned managed records behind MCP."""

import hashlib
import time
import uuid
from datetime import UTC, datetime

from backend.app.core.principal import current_principal
from backend.app.store.azure_records import CosmosRecords


class CosmosCaseMemoryStore:
    def __init__(self, records: CosmosRecords):
        self.records = records

    def save_case(self, case_id: str, state: dict):
        previous = self.records.read("case", case_id)
        self.records.write("case", case_id, state, previous=previous, create_only=previous is None)

    def load_case(self, case_id: str):
        self.require_voice_owner(case_id)
        row = self.records.read("case", case_id)
        return row["payload"] if row else None

    def require_voice_owner(self, case_id):
        if case_id.startswith("voice-"):
            row = self.records.read("voice", case_id.removeprefix("voice-"))
            if not row or row.get("owner_id") != current_principal(self.records.settings).owner_id:
                raise PermissionError("Case access is unavailable for this account")

    def list_cases(self):
        visible = []
        for row in self.records.query("case"):
            try:
                self.require_voice_owner(row["key"])
            except PermissionError:
                continue
            visible.append(row["key"])
        return visible

    def save_fact(self, fact: dict):
        identity = hashlib.sha256(
            str(
                (
                    fact.get("case_id"),
                    fact["subject"],
                    fact["predicate"],
                    fact["value"],
                    fact["source"],
                )
            ).encode()
        ).hexdigest()
        if self.records.read("fact", identity):
            return
        self.records.write(
            "fact",
            identity,
            fact,
            fields={"case_id": fact.get("case_id"), "subject": fact["subject"]},
        )

    def search_facts(self, subject: str, query: str | None = None):
        rows = [row["payload"] for row in self.records.query("fact", fields={"subject": subject})]
        if query:
            rows = [row for row in rows if query.casefold() in str(row).casefold()]
        return sorted(rows, key=lambda row: row["observed_at"], reverse=True)

    def list_facts_for_case(self, case_id: str):
        return [row["payload"] for row in self.records.query("fact", fields={"case_id": case_id})]

    def record_event(
        self,
        case_id: str,
        tool: str | None,
        source: str,
        content: str,
        event_key: str | None = None,
    ):
        identifier = event_key or uuid.uuid4().hex
        key = case_id + ":" + identifier
        if self.records.read("event", key):
            return
        payload = {
            "id": time.time_ns() // 1000,
            "tool": tool,
            "source": source,
            "content": content,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self.records.write("event", key, payload, fields={"case_id": case_id})

    def list_events(self, case_id: str):
        self.require_voice_owner(case_id)
        return sorted(
            [row["payload"] for row in self.records.query("event", fields={"case_id": case_id})],
            key=lambda row: row["id"],
        )
