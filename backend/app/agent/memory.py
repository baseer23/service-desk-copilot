from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


class CaseMemoryStore:
    """Lightweight SQLite-backed operational memory for service-desk cases."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else Path("store/case_memory.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    value TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_system TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.0,
                    reliability TEXT NOT NULL DEFAULT 'confirmed',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL,
                    tool TEXT,
                    source TEXT,
                    content TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
            if "event_key" not in columns:
                conn.execute("ALTER TABLE events ADD COLUMN event_key TEXT")
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS event_key_unique ON events(case_id,event_key) WHERE event_key IS NOT NULL"
            )
            conn.commit()

    def save_case(self, case_id: str, state: dict[str, Any]) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO cases(case_id, state_json, updated_at)
                VALUES(?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(case_id)
                DO UPDATE SET state_json = excluded.state_json, updated_at = CURRENT_TIMESTAMP
                """,
                (case_id, json.dumps(state, default=str)),
            )
            conn.commit()

    def insert_voice_case(
        self, conn: sqlite3.Connection, case_id: str, intake: Any, summary: str | None
    ) -> None:
        """Use the voice repository's transaction; never overwrite an existing case."""
        state = {
            "case_id": case_id,
            "ticket": {"summary": summary or intake.final_summary or intake.issue_summary},
            "status": "new",
            "confidence": 0,
            "tenant_id": intake.tenant_id,
            "voice_intake": {
                "session_id": intake.session_id,
                "contact": intake.confirmed_profile,
                "confirmed": True,
                "resolution_status": intake.resolution_status,
                "evidence_ids": [e.id for e in intake.evidence],
                "actions": [a.model_dump() for a in intake.actions],
            },
        }
        existing = conn.execute(
            "SELECT state_json FROM cases WHERE case_id=?", (case_id,)
        ).fetchone()
        if existing:
            prior = json.loads(existing[0])
            if (
                prior.get("voice_intake", {}).get("session_id") != intake.session_id
                or prior.get("tenant_id") != intake.tenant_id
            ):
                raise ValueError("Linked case provenance does not match the voice session")
            prior["voice_intake"] = state["voice_intake"]
            prior["voice_intake"]["final_summary"] = intake.final_summary
            if prior.get("status") == "new":
                prior["ticket"] = state["ticket"]
            conn.execute(
                "UPDATE cases SET state_json=? WHERE case_id=?", (json.dumps(prior), case_id)
            )
        conn.execute(
            "INSERT OR IGNORE INTO cases(case_id,state_json) VALUES(?,?)",
            (case_id, json.dumps(state)),
        )
        conn.execute(
            "INSERT OR IGNORE INTO events(case_id,tool,source,content,event_key) VALUES(?,?,?,?,?)",
            (
                case_id,
                "voice_intake",
                "human_confirmed",
                "Contact details explicitly confirmed during voice intake.",
                "voice-intake:" + intake.session_id,
            ),
        )

    def load_case(self, case_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT state_json FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            if row is None:
                return None
            return json.loads(row["state_json"])

    def save_fact(self, fact: dict[str, Any]) -> None:
        with closing(self._connect()) as conn:
            existing = conn.execute(
                "SELECT id FROM facts WHERE case_id=? AND subject=? AND predicate=? AND value=? AND source=?",
                (
                    fact.get("case_id"),
                    fact["subject"],
                    fact["predicate"],
                    fact["value"],
                    fact["source"],
                ),
            ).fetchone()
            if existing:
                return
            conn.execute(
                """
                INSERT INTO facts(case_id, subject, predicate, value, source, source_system, observed_at, confidence, reliability, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact.get("case_id"),
                    fact["subject"],
                    fact["predicate"],
                    fact["value"],
                    fact["source"],
                    fact.get("source_system", "local_fixture"),
                    fact["observed_at"],
                    float(fact.get("confidence", 0.0)),
                    fact.get("reliability", "confirmed"),
                    json.dumps(fact.get("metadata", {}), default=str),
                ),
            )
            conn.commit()

    def search_facts(self, subject: str, query: str | None = None) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            if query:
                rows = conn.execute(
                    """
                    SELECT * FROM facts
                    WHERE subject = ? AND (
                        predicate LIKE ? OR value LIKE ? OR source LIKE ? OR metadata_json LIKE ?
                    )
                    ORDER BY observed_at DESC, id DESC
                    """,
                    (subject, f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%"),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM facts WHERE subject = ? ORDER BY observed_at DESC, id DESC",
                    (subject,),
                ).fetchall()
            results = []
            for row in rows:
                results.append(
                    {
                        "subject": row["subject"],
                        "predicate": row["predicate"],
                        "value": row["value"],
                        "source": row["source"],
                        "case_id": row["case_id"],
                        "source_system": row["source_system"],
                        "observed_at": row["observed_at"],
                        "confidence": row["confidence"],
                        "reliability": row["reliability"],
                        "metadata": json.loads(row["metadata_json"]),
                    }
                )
            return results

    def record_event(
        self,
        case_id: str,
        tool: str | None,
        source: str,
        content: str,
        event_key: str | None = None,
    ) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO events(case_id, tool, source, content, event_key) VALUES (?, ?, ?, ?, ?)",
                (case_id, tool, source, content, event_key),
            )
            conn.commit()

    def list_events(self, case_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE case_id = ? ORDER BY id ASC",
                (case_id,),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "tool": row["tool"],
                    "source": row["source"],
                    "content": row["content"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def list_cases(self) -> list[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT case_id FROM cases ORDER BY updated_at DESC").fetchall()
            return [row["case_id"] for row in rows]

    def list_facts_for_case(self, case_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM facts WHERE case_id = ? ORDER BY observed_at DESC, id DESC",
                (case_id,),
            ).fetchall()
            return [
                {
                    "subject": row["subject"],
                    "predicate": row["predicate"],
                    "value": row["value"],
                    "source": row["source"],
                    "case_id": row["case_id"],
                    "source_system": row["source_system"],
                    "observed_at": row["observed_at"],
                    "confidence": row["confidence"],
                    "reliability": row["reliability"],
                    "metadata": json.loads(row["metadata_json"]),
                }
                for row in rows
            ]
