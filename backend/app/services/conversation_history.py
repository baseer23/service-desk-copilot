"""Durable owner-scoped index for LangGraph conversation threads."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.app.core.principal import Principal, current_principal
from backend.app.store.local_records import LocalRecords


class ConversationHistory:
    """Index conversation metadata without duplicating checkpoint state."""

    def __init__(self, settings, records: LocalRecords | None = None):
        self.settings = settings
        self.records = records or LocalRecords(settings)

    def save_turn(
        self,
        conversation_id: str,
        state: dict,
        *,
        principal: Principal | None = None,
    ) -> dict:
        actor = principal or current_principal(self.settings)
        previous = self.records.read("conversation", conversation_id, tenant=actor.tenant_id)
        if previous and previous.get("owner_id") != actor.owner_id:
            raise PermissionError("Conversation ownership mismatch")
        now = datetime.now(timezone.utc).isoformat()
        old = previous["payload"] if previous else {}
        messages = state.get("chat_messages") or []
        preview = next(
            (str(item.get("content", "")) for item in reversed(messages) if item.get("content")),
            state.get("final_answer") or state.get("ticket", {}).get("summary", ""),
        )
        payload = {
            "schema_version": 1,
            "conversation_id": conversation_id,
            "owner_id": actor.owner_id,
            "title": old.get("title") or state.get("ticket", {}).get("summary", "Untitled conversation")[:160],
            "workflow": state.get("workflow") or old.get("workflow") or "knowledge",
            "status": state.get("status", "active"),
            "created_at": old.get("created_at", now),
            "updated_at": now,
            "linked_case_id": conversation_id if state.get("persist_case") else None,
            "last_message_preview": str(preview)[:240],
            "activity": old.get("activity", []),
        }
        return self.records.write(
            "conversation",
            conversation_id,
            payload,
            previous=previous,
            create_only=previous is None,
            tenant=actor.tenant_id,
            fields={"owner_id": actor.owner_id, "status": payload["status"]},
        )["payload"]

    def save_activity(
        self, conversation_id: str, activity: list[dict], *, principal: Principal | None = None
    ) -> None:
        actor = principal or current_principal(self.settings)
        previous = self.records.read("conversation", conversation_id, tenant=actor.tenant_id)
        if not previous or previous.get("owner_id") != actor.owner_id:
            return
        payload = {**previous["payload"], "activity": activity[-200:]}
        self.records.write(
            "conversation", conversation_id, payload, previous=previous,
            tenant=actor.tenant_id,
            fields={"owner_id": actor.owner_id, "status": payload["status"]},
        )

    def list(self, *, principal: Principal | None = None, limit: int = 100) -> list[dict]:
        actor = principal or current_principal(self.settings)
        rows = self.records.query(
            "conversation",
            tenant=actor.tenant_id,
            fields={"owner_id": actor.owner_id},
            limit=min(max(limit, 1), 200),
        )
        return [row["payload"] for row in rows if row.get("owner_id") == actor.owner_id]

    def get(self, conversation_id: str, *, principal: Principal | None = None) -> dict | None:
        actor = principal or current_principal(self.settings)
        row = self.records.read("conversation", conversation_id, tenant=actor.tenant_id)
        if not row or row.get("owner_id") != actor.owner_id:
            return None
        return row["payload"]
