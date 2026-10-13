"""Transactional browser conversations; obsolete operational tables stay untouched."""

import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from .auth import Actor, VoiceError
from .models import TERMINAL, VoiceConversation, VoiceStatus, utcnow


class VoiceConversationRepository(Protocol):
    def create(self, actor: Actor) -> VoiceConversation: ...
    def get(self, session_id: str, actor: Actor) -> VoiceConversation: ...
    def claim(self, session_id: str, actor: Actor, lease: str) -> VoiceConversation: ...
    def renew(self, session_id: str, lease: str) -> None: ...
    def update(
        self,
        session_id: str,
        actor: Actor,
        fn: Callable[[VoiceConversation], None],
        lease: str | None = None,
        event_id: str | None = None,
    ) -> VoiceConversation: ...
    def release(self, session_id: str, actor: Actor, lease: str) -> None: ...
    def events(self, session_id: str, actor: Actor) -> list[dict[str, Any]]: ...
    def create_case(self, session_id: str, actor: Actor, store: Any) -> str: ...
    def purge(self, tenant: str, cutoff: str) -> None: ...


class SQLiteVoiceConversationRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript(
                """
            CREATE TABLE IF NOT EXISTS voice_sessions (
                session_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, owner_id TEXT NOT NULL,
                status TEXT NOT NULL, lease_id TEXT, lease_until REAL NOT NULL DEFAULT 0,
                version INTEGER NOT NULL, state_json TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS voice_session_owner
                ON voice_sessions(tenant_id,owner_id,status);
            CREATE TABLE IF NOT EXISTS voice_session_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                type TEXT NOT NULL, label TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(session_id,event_id));
            CREATE TABLE IF NOT EXISTS voice_transcript_turns (
                session_id TEXT NOT NULL, id TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL,
                created_at TEXT NOT NULL, UNIQUE(session_id,id));
            """
            )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _read(conn: sqlite3.Connection, session_id: str, actor: Actor) -> VoiceConversation:
        row = conn.execute(
            "SELECT state_json FROM voice_sessions WHERE session_id=? AND owner_id=? AND"
            " tenant_id=?",
            (session_id, actor.owner_id, actor.tenant_id),
        ).fetchone()
        if row is None:
            raise VoiceError("Voice session not found", 404)
        return VoiceConversation.model_validate_json(row[0])

    def get(self, session_id: str, actor: Actor) -> VoiceConversation:
        with self.connection() as conn:
            return self._read(conn, session_id, actor)

    def create(self, actor: Actor) -> VoiceConversation:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for row in conn.execute(
                "SELECT state_json FROM voice_sessions WHERE owner_id=? AND tenant_id=? AND status"
                " NOT IN ('completed','failed','stopped') AND lease_until<?",
                (actor.owner_id, actor.tenant_id, time.time() - 30),
            ).fetchall():
                previous = VoiceConversation.model_validate_json(row[0])
                if datetime.fromisoformat(previous.updated_at).timestamp() < time.time() - 30:
                    previous.status = VoiceStatus.FAILED
                    previous.failure_category = "abandoned_connection"
                    previous.ended_at = utcnow()
                    previous.version += 1
                    conn.execute(
                        "UPDATE voice_sessions SET status=?,version=?,state_json=? WHERE"
                        " session_id=?",
                        (
                            previous.status.value,
                            previous.version,
                            previous.model_dump_json(),
                            previous.session_id,
                        ),
                    )
            capacity = conn.execute(
                "SELECT count(*) FROM voice_sessions WHERE lease_until>?", (time.time(),)
            ).fetchone()[0]
            if capacity >= 16:
                raise VoiceError("Voice session capacity is reached", 503)
            active = conn.execute(
                "SELECT count(*) FROM voice_sessions WHERE owner_id=? AND tenant_id=? AND status"
                " NOT IN ('completed','failed','stopped')",
                (actor.owner_id, actor.tenant_id),
            ).fetchone()[0]
            leased = conn.execute(
                "SELECT count(*) FROM voice_sessions "
                "WHERE owner_id=? AND tenant_id=? AND lease_until>?",
                (actor.owner_id, actor.tenant_id, time.time()),
            ).fetchone()[0]
            if leased:
                raise VoiceError("Wait for the previous voice connection to finish closing")
            if active:
                raise VoiceError("Stop the current voice session before starting another")
            state = VoiceConversation(
                session_id=uuid.uuid4().hex,
                owner_id=actor.owner_id,
                tenant_id=actor.tenant_id,
                status=VoiceStatus.CONNECTING,
            )
            conn.execute(
                "INSERT INTO"
                " voice_sessions(session_id,tenant_id,owner_id,status,version,state_json)"
                " VALUES(?,?,?,?,?,?)",
                (
                    state.session_id,
                    state.tenant_id,
                    state.owner_id,
                    state.status.value,
                    0,
                    state.model_dump_json(),
                ),
            )
            return state

    def claim(self, session_id: str, actor: Actor, lease: str) -> VoiceConversation:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = self._read(conn, session_id, actor)
            another = conn.execute(
                "SELECT count(*) FROM voice_sessions "
                "WHERE owner_id=? AND tenant_id=? AND session_id<>? "
                "AND (status NOT IN ('completed','failed','stopped') OR lease_until>?)",
                (actor.owner_id, actor.tenant_id, session_id, time.time()),
            ).fetchone()[0]
            if another:
                raise VoiceError("Stop the other voice conversation before reconnecting")
            row = conn.execute(
                "SELECT lease_id,lease_until FROM voice_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            if row["lease_id"] and row["lease_until"] > time.time():
                raise VoiceError("This voice session already has a connected client")
            if state.status == VoiceStatus.COMPLETED or state.failure_category == "explicit_stop":
                raise VoiceError("Start a new conversation after an explicit stop or completion")
            state.status = VoiceStatus.CONNECTING
            state.ended_at = None
            state.failure_category = None
            state.version += 1
            conn.execute(
                "UPDATE voice_sessions SET lease_id=?,lease_until=?,status=?,version=?,state_json=?"
                " WHERE session_id=?",
                (
                    lease,
                    time.time() + 20,
                    state.status.value,
                    state.version,
                    state.model_dump_json(),
                    session_id,
                ),
            )
            return state

    def renew(self, session_id: str, lease: str) -> None:
        with self.connection() as conn:
            row = conn.execute(
                "UPDATE voice_sessions SET lease_until=? WHERE session_id=? AND lease_id=?",
                (time.time() + 20, session_id, lease),
            )
            if row.rowcount != 1:
                raise VoiceError("Voice connection ownership was lost")

    def update(
        self,
        session_id: str,
        actor: Actor,
        fn: Callable[[VoiceConversation], None],
        lease: str | None = None,
        event_id: str | None = None,
    ) -> VoiceConversation:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = self._read(conn, session_id, actor)
            row = conn.execute(
                "SELECT lease_id,lease_until FROM voice_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            if lease and (row["lease_id"] != lease or row["lease_until"] < time.time()):
                raise VoiceError("Stale voice connection")
            if (
                event_id
                and conn.execute(
                    "SELECT 1 FROM voice_session_events WHERE session_id=? AND event_id=?",
                    (session_id, event_id),
                ).fetchone()
            ):
                return state
            previous = state.status
            fn(state)
            if previous in TERMINAL and state.status not in TERMINAL:
                raise VoiceError("A terminal session cannot accept a late update")
            state.version += 1
            state.updated_at = utcnow()
            if state.status in TERMINAL:
                state.ended_at = state.ended_at or state.updated_at
            conn.execute(
                "UPDATE voice_sessions SET status=?,version=?,state_json=? WHERE session_id=?",
                (state.status.value, state.version, state.model_dump_json(), session_id),
            )
            if event_id:
                conn.execute(
                    "INSERT INTO voice_session_events(session_id,event_id,type,label,created_at)"
                    " VALUES(?,?,?,?,?)",
                    (
                        session_id,
                        event_id,
                        "status",
                        state.status.value.replace("_", " "),
                        utcnow(),
                    ),
                )
            for turn in state.turns:
                conn.execute(
                    "INSERT INTO voice_transcript_turns VALUES(?,?,?,?,?) ON"
                    " CONFLICT(session_id,id) DO UPDATE SET text=excluded.text",
                    (session_id, turn.id, turn.role, turn.text, turn.created_at),
                )
            return state

    def release(self, session_id: str, actor: Actor, lease: str) -> None:
        with self.connection() as conn:
            self._read(conn, session_id, actor)
            conn.execute(
                "UPDATE voice_sessions SET lease_id=NULL,lease_until=0 WHERE session_id=? AND"
                " lease_id=?",
                (session_id, lease),
            )

    def events(self, session_id: str, actor: Actor) -> list[dict[str, Any]]:
        with self.connection() as conn:
            self._read(conn, session_id, actor)
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT type,label,created_at FROM voice_session_events WHERE session_id=?"
                    " ORDER BY id DESC LIMIT 200",
                    (session_id,),
                )
            ][::-1]

    def create_case(self, session_id: str, actor: Actor, store: Any) -> str:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = self._read(conn, session_id, actor)
            if (
                not all(
                    state.confirmed_profile.get(key) for key in ("name", "email", "callback_number")
                )
                or not state.issue_summary
            ):
                raise VoiceError(
                    "Confirm contact details and describe the issue before saving a case"
                )
            case_id = "voice-" + session_id
            store.insert_voice_case(conn, case_id, state, None)
            state.case_id = case_id
            state.version += 1
            state.updated_at = utcnow()
            conn.execute(
                "UPDATE voice_sessions SET state_json=?,version=? WHERE session_id=?",
                (state.model_dump_json(), state.version, session_id),
            )
            return case_id

    def purge(self, tenant: str, cutoff: str) -> None:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT state_json FROM voice_sessions WHERE tenant_id=? AND status IN"
                " ('completed','failed','stopped') AND json_extract(state_json,'$.ended_at')<?",
                (tenant, cutoff),
            ).fetchall()
            for row in rows:
                state = VoiceConversation.model_validate_json(row[0])
                if not state.turns:
                    continue
                state.turns = []
                for candidate in state.candidates.values():
                    candidate.raw_quote = ""
                state.version += 1
                conn.execute(
                    "UPDATE voice_sessions SET state_json=?,version=? WHERE session_id=?",
                    (state.model_dump_json(), state.version, state.session_id),
                )
                conn.execute(
                    "DELETE FROM voice_transcript_turns WHERE session_id=?", (state.session_id,)
                )
