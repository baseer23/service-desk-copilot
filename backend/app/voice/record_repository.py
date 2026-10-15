"""Local record-backed browser conversations with leases, replay checks and CAS updates."""

import copy
import time
import uuid
from datetime import datetime

from backend.app.store.records import ManagedStateConflict, ManagedStoreError, record_id
from backend.app.store.local_records import LocalRecords

from .auth import Actor, VoiceError
from .models import TERMINAL, VoiceConversation, VoiceStatus, utcnow


class RecordVoiceConversationRepository:
    def __init__(self, records: LocalRecords):
        self.records = records

    def _read(self, session_id: str, actor: Actor):
        row = self.records.read("voice", session_id, tenant=actor.tenant_id)
        if not row or row.get("owner_id") != actor.owner_id:
            raise VoiceError("Voice session not found", 404)
        return row, VoiceConversation.model_validate(row["payload"]["state"])

    def get(self, session_id: str, actor: Actor):
        return self._read(session_id, actor)[1]

    def create(self, actor: Actor):
        with self.records.locked("voice_owner", actor.owner_id, tenant=actor.tenant_id) as lane:
            existing = lane.get("session_id")
            if existing:
                row, previous = self._read(existing, actor)
                if previous.status not in TERMINAL:
                    if (
                        row["payload"].get("lease_until", 0) > time.time()
                        or datetime.fromisoformat(previous.updated_at).timestamp()
                        > time.time() - 30
                    ):
                        raise VoiceError("Stop the other support conversation first")
                    previous.status = VoiceStatus.FAILED
                    previous.failure_category = "abandoned_connection"
                    previous.ended_at = utcnow()
                    self.records.write(
                        "voice",
                        existing,
                        {**row["payload"], "state": previous.model_dump(mode="json")},
                        previous=row,
                        tenant=actor.tenant_id,
                        fields={"owner_id": actor.owner_id},
                    )
            state = VoiceConversation(
                session_id=uuid.uuid4().hex,
                tenant_id=actor.tenant_id,
                owner_id=actor.owner_id,
                status=VoiceStatus.CONNECTING,
            )
            self.records.write(
                "voice",
                state.session_id,
                {
                    "state": state.model_dump(mode="json"),
                    "lease_id": "",
                    "lease_until": 0,
                    "events": [],
                },
                tenant=actor.tenant_id,
                create_only=True,
                fields={"owner_id": actor.owner_id},
            )
            lane["session_id"] = state.session_id
            return state

    def claim(self, session_id: str, actor: Actor, lease: str):
        lane = self.records.read("voice_owner", actor.owner_id, tenant=actor.tenant_id)
        if not lane or lane["payload"].get("session_id") != session_id:
            raise VoiceError("Start a new support conversation")
        row, state = self._read(session_id, actor)
        payload = row["payload"]
        if (
            payload.get("lease_until", 0) > time.time()
            or state.status == VoiceStatus.COMPLETED
            or (state.status == VoiceStatus.STOPPED and not state.failure_category)
            or state.failure_category == "explicit_stop"
        ):
            raise VoiceError("This support conversation cannot be reconnected")
        state.status = VoiceStatus.CONNECTING
        state.failure_category = None
        state.ended_at = None
        state.version += 1
        self.records.write(
            "voice",
            session_id,
            {
                **payload,
                "state": state.model_dump(mode="json"),
                "lease_id": lease,
                "lease_until": time.time() + 20,
            },
            previous=row,
            tenant=actor.tenant_id,
            fields={"owner_id": actor.owner_id},
        )
        return state

    def renew(self, session_id: str, lease: str):
        row = self.records.read("voice", session_id)
        if (
            not row
            or row["payload"].get("lease_id") != lease
            or row["payload"].get("lease_until", 0) < time.time()
        ):
            raise VoiceError("Voice connection ownership was lost")
        self.records.write(
            "voice",
            session_id,
            {**row["payload"], "lease_until": time.time() + 20},
            previous=row,
            fields={"owner_id": row["owner_id"]},
        )

    def update(self, session_id, actor, fn, lease=None, event_id=None):
        row, state = self._read(session_id, actor)
        payload = row["payload"]
        if lease and (
            payload.get("lease_id") != lease or payload.get("lease_until", 0) < time.time()
        ):
            raise VoiceError("Stale voice connection", 409, "VOICE_LEASE_LOST")
        events = list(payload.get("events", []))
        if event_id and any(event["event_id"] == event_id for event in events):
            return state
        previous = state.status
        fn(state)
        if previous in TERMINAL and state.status not in TERMINAL:
            raise VoiceError("A terminal session cannot accept a late update")
        if previous in {VoiceStatus.COMPLETED, VoiceStatus.STOPPED} and state.status != previous:
            raise VoiceError("A terminal session cannot change its final outcome")
        state.version += 1
        state.updated_at = utcnow()
        if state.status in TERMINAL:
            state.ended_at = state.ended_at or state.updated_at
        if event_id:
            events.append(
                {
                    "event_id": event_id,
                    "type": "status",
                    "label": state.status.value.replace("_", " "),
                    "created_at": utcnow(),
                }
            )
        try:
            self.records.write(
                "voice",
                session_id,
                {**payload, "state": state.model_dump(mode="json"), "events": events[-2000:]},
                previous=row,
                tenant=actor.tenant_id,
                fields={"owner_id": actor.owner_id},
            )
        except ManagedStoreError as error:
            raise VoiceError(
                "The conversation changed; retry the action", 409,
                "VOICE_STATE_CONFLICT" if isinstance(error, ManagedStateConflict)
                else "VOICE_STATE_STORE_ERROR",
            ) from None
        return state

    def release(self, session_id, actor, lease):
        row, _ = self._read(session_id, actor)
        if row["payload"].get("lease_id") == lease:
            self.records.write(
                "voice",
                session_id,
                {**row["payload"], "lease_id": "", "lease_until": 0},
                previous=row,
                tenant=actor.tenant_id,
                fields={"owner_id": actor.owner_id},
            )

    def events(self, session_id, actor):
        row, _ = self._read(session_id, actor)
        return [
            {key: event[key] for key in ("type", "label", "created_at")}
            for event in row["payload"].get("events", [])[-200:]
        ]

    def create_case(self, session_id, actor, store):
        row, state = self._read(session_id, actor)
        if (
            not all(
                state.confirmed_profile.get(key) for key in ("name", "email", "callback_number")
            )
            or not state.issue_summary
        ):
            raise VoiceError("Confirm contact details and describe the issue before saving a case")
        case_id = "voice-" + session_id
        previous = self.records.read("case", case_id, tenant=actor.tenant_id)
        case = (
            copy.deepcopy(previous["payload"])
            if previous
            else {
                "case_id": case_id,
                "ticket": {"summary": state.final_summary or state.issue_summary},
                "status": "new",
                "confidence": 0,
                "tenant_id": actor.tenant_id,
            }
        )
        if previous and case.get("voice_intake", {}).get("session_id") != session_id:
            raise VoiceError("Linked case provenance does not match this conversation")
        case["voice_intake"] = {
            "session_id": session_id,
            "contact": state.confirmed_profile,
            "confirmed": True,
            "resolution_status": state.resolution_status,
            "evidence_ids": [item.id for item in state.evidence],
            "actions": [item.model_dump(mode="json") for item in state.actions],
            "final_summary": state.final_summary,
        }
        state.case_id = case_id
        state.version += 1
        state.updated_at = utcnow()
        voice_document = {
            **row,
            "payload": {**row["payload"], "state": state.model_dump(mode="json")},
        }
        voice_document = {
            key: value for key, value in voice_document.items() if not key.startswith("_")
        }
        case_document = {
            "id": record_id("case", case_id),
            "tenant_id": actor.tenant_id,
            "kind": "case",
            "key": case_id,
            "payload": case,
            "updated_at": time.time(),
        }
        operation = (
            ("replace", (previous["id"], case_document), {"if_match_etag": previous["_etag"]})
            if previous
            else ("create", (case_document,), {})
        )
        event_key = case_id + ":voice-intake:" + session_id
        event = self.records.read("event", event_key, tenant=actor.tenant_id)
        operations = [
            ("replace", (row["id"], voice_document), {"if_match_etag": row["_etag"]}),
            operation,
        ]
        if not event:
            event_document = {
                "id": record_id("event", event_key),
                "tenant_id": actor.tenant_id,
                "kind": "event",
                "key": event_key,
                "case_id": case_id,
                "updated_at": time.time(),
                "payload": {
                    "id": time.time_ns() // 1000,
                    "tool": "voice_intake",
                    "source": "human_confirmed",
                    "content": "Contact details explicitly confirmed during support intake.",
                    "created_at": utcnow(),
                },
            }
            operations.append(("create", (event_document,), {}))
        with self.records.locked("record_quota", "tenant", tenant=actor.tenant_id) as quota:
            import json

            sizes = quota.setdefault("objects", {})
            for _, arguments, _ in operations:
                document = arguments[-1]
                size = len(json.dumps(document).encode())
                if (
                    size > 1_800_000
                    or sum(sizes.values()) - sizes.get(document["id"], 0) + size > 100_000_000
                    or (document["id"] not in sizes and len(sizes) >= 5000)
                ):
                    raise VoiceError("Local data capacity is full")
                sizes[document["id"]] = max(size, sizes.get(document["id"], 0))
        try:
            self.records.atomic_batch(operations, tenant=actor.tenant_id)
        except Exception:  # noqa: BLE001 - Sanitize SDK/auth failures without disclosing response bodies.
            raise VoiceError("The conversation and case could not be saved atomically") from None
        return case_id

    def purge(self, tenant, cutoff):
        for row in self.records.query("voice", tenant=tenant):
            state = VoiceConversation.model_validate(row["payload"]["state"])
            if (
                state.status in TERMINAL
                and state.ended_at
                and state.ended_at < cutoff
                and state.turns
            ):
                state.turns = []
                for candidate in state.candidates.values():
                    candidate.raw_quote = ""
                self.records.write(
                    "voice",
                    state.session_id,
                    {**row["payload"], "state": state.model_dump(mode="json")},
                    previous=row,
                    tenant=tenant,
                    fields={"owner_id": state.owner_id},
                )
