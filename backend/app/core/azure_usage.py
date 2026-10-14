"""Local conservative CAD estimates. This cannot cap an Azure subscription invoice.

Reserve before dispatch; finalize token estimates only with complete usage counters.
Retain reservations for failed/unknown usage and the full speech duration allowance.
The shared SQLite file coordinates API/MCP processes and survives restarts. A crash
leaves a session active deliberately: inspect it privately before any recovery.
"""

import hashlib
import json
import math
import os
import re
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.core.config import Settings


class AzureUsageBlocked(RuntimeError):
    """A safe, operator-visible local policy rejection."""


class AzureUsageGuard:
    def __new__(cls, settings: Settings, *args, **kwargs):
        if cls is AzureUsageGuard and settings.data_platform == "azure":
            from backend.app.core.azure_managed_usage import CosmosUsageGuard

            return object.__new__(CosmosUsageGuard)
        return object.__new__(cls)

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.azure_usage_db_path or settings.case_db_path.with_name(
            settings.case_db_path.name + ".azure-usage.sqlite3"
        )

    def ledger_exists(self) -> bool:
        return self.path.exists()

    def earmark_infrastructure(self, reference: str, cad_estimate: float) -> None:
        """Reserve a bounded hosting/storage forecast without enabling AI dispatch."""
        if (
            not math.isfinite(cad_estimate)
            or not 0 <= cad_estimate <= self.settings.azure_estimated_monthly_app_limit_cad
            or not re.fullmatch(r"[a-z0-9-]{1,64}", reference)
        ):
            raise ValueError("Unsupported infrastructure forecast")
        now = datetime.now(UTC)
        month = now.strftime("%Y-%m")
        key = "infrastructure-" + month + "-" + reference
        estimate = math.ceil(cad_estimate * 1_000_000)
        with self.transaction() as db:
            existing = db.execute("SELECT estimate FROM azure_usage WHERE id=?", (key,)).fetchone()
            spent = db.execute(
                "SELECT COALESCE(SUM(estimate),0) FROM azure_usage WHERE month=?", (month,)
            ).fetchone()[0]
            previous = existing[0] if existing else 0
            increase = max(0, estimate - previous)
            if spent + increase > self.effective_limit(db):
                raise AzureUsageBlocked("Infrastructure forecast exceeds the application allowance")
            if not existing:
                db.execute(
                    "INSERT INTO azure_usage(id,month,day,kind,estimate,active,phase) VALUES(?,?,?,?,?,0,'demo')",
                    (key, month, now.strftime("%Y-%m-%d"), "infrastructure", estimate),
                )
            elif increase:
                db.execute("UPDATE azure_usage SET estimate=? WHERE id=?", (estimate, key))

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            except FileExistsError:
                pass
            connection = sqlite3.connect(self.path, timeout=5)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS azure_policy "
                "(id INTEGER PRIMARY KEY, disabled INTEGER NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS azure_demo_policy "
                "(id INTEGER PRIMARY KEY, limit_micros INTEGER NOT NULL, "
                "authorized_at REAL NOT NULL, authorization_reference TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS azure_usage "
                "(id TEXT PRIMARY KEY, month TEXT NOT NULL, day TEXT NOT NULL, kind TEXT NOT NULL, "
                "estimate INTEGER NOT NULL, active INTEGER NOT NULL, "
                "usage TEXT NOT NULL DEFAULT '{}',"
                " seconds REAL NOT NULL DEFAULT 0, turns INTEGER NOT NULL DEFAULT 0)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS azure_voice_usage "
                "(reservation TEXT NOT NULL, event TEXT NOT NULL, usage TEXT NOT NULL, "
                "PRIMARY KEY(reservation,event))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS azure_acceptance_fixture "
                "(id INTEGER PRIMARY KEY CHECK(id=1), digest TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS azure_acceptance_operator "
                "(id INTEGER PRIMARY KEY CHECK(id=1), digest TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS azure_dispatch_slots "
                "(kind TEXT PRIMARY KEY, next_at REAL NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS azure_validation "
                "(capability TEXT PRIMARY KEY, passed INTEGER NOT NULL, code TEXT NOT NULL)"
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(azure_usage)")}
            if "phase" not in columns:
                connection.execute(
                    "ALTER TABLE azure_usage ADD COLUMN phase TEXT NOT NULL DEFAULT 'demo'"
                )
            yield connection
            connection.commit()
        except (sqlite3.Error, OSError):
            if connection:
                connection.rollback()
            raise AzureUsageBlocked(
                "Azure usage ledger unavailable; Azure operations blocked"
            ) from None
        except BaseException:
            if connection:
                connection.rollback()
            raise
        finally:
            if connection:
                connection.close()

    def _allowed(self, db: sqlite3.Connection) -> None:
        disabled = db.execute("SELECT disabled FROM azure_policy WHERE id=1").fetchone()
        if (
            not self.settings.azure_usage_guard_enabled
            or self.settings.azure_emergency_disable
            or (disabled and disabled[0])
        ):
            raise AzureUsageBlocked("Azure usage disabled; explicit local enablement required")

    @contextmanager
    def inspection(self):
        with self.transaction() as db:
            yield db

    def require_enabled(self) -> None:
        if not self.settings.azure_usage_guard_enabled:
            raise AzureUsageBlocked("Azure usage disabled; explicit local enablement required")
        with self.transaction() as db:
            self._allowed(db)
            month = datetime.now(UTC).strftime("%Y-%m")
            spent = db.execute(
                "SELECT COALESCE(SUM(estimate),0) FROM azure_usage WHERE month=?", (month,)
            ).fetchone()[0]
            if spent >= self.effective_limit(db):
                raise AzureUsageBlocked("Estimated monthly Azure application allowance exhausted")
            if self.settings.azure_setup_validation_mode:
                setup = db.execute(
                    "SELECT COALESCE(SUM(estimate),0) FROM azure_usage WHERE phase='setup'"
                ).fetchone()[0]
                if setup >= self.settings.azure_estimated_setup_limit_cad * 1_000_000:
                    raise AzureUsageBlocked("Estimated Azure setup validation allowance exhausted")

    def request_delay(self, kind: str) -> float:
        self.require_enabled()
        interval = (
            self.settings.azure_reasoning_min_interval_seconds
            if kind == "reasoning"
            else self.settings.azure_embedding_min_interval_seconds
        )
        if not interval:
            return 0
        with self.transaction() as db:
            self._allowed(db)
            now = time.time()
            row = db.execute(
                "SELECT next_at FROM azure_dispatch_slots WHERE kind=?", (kind,)
            ).fetchone()
            if row and row[0] > now:
                return float(row[0] - now)
            db.execute(
                "INSERT INTO azure_dispatch_slots VALUES(?,?) "
                "ON CONFLICT(kind) DO UPDATE SET next_at=excluded.next_at",
                (kind, now + interval),
            )
            return 0

    @property
    def limit(self) -> int:
        return math.floor(self.settings.azure_estimated_monthly_app_limit_cad * 1_000_000)

    def effective_limit(self, db: sqlite3.Connection) -> int:
        row = db.execute("SELECT limit_micros FROM azure_demo_policy WHERE id=1").fetchone()
        return min(self.limit, row[0]) if row else self.limit

    def enable_demo(self, limit_cad: float, authorization_reference: str) -> None:
        """Explicit operator activation; preserve all charges and active reservations."""
        if (
            isinstance(limit_cad, bool)
            or not math.isfinite(limit_cad)
            or not 0 < limit_cad <= self.settings.azure_estimated_monthly_app_limit_cad
            or not re.fullmatch(r"[a-z0-9-]{1,64}", authorization_reference)
        ):
            raise ValueError("Invalid authorized demo allowance")
        if not self.settings.azure_usage_guard_enabled or self.settings.azure_emergency_disable:
            raise AzureUsageBlocked("Enable the configured guard before operator activation")
        with self.transaction() as db:
            db.execute(
                "INSERT INTO azure_demo_policy VALUES(1,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET limit_micros=excluded.limit_micros, "
                "authorized_at=excluded.authorized_at, "
                "authorization_reference=excluded.authorization_reference",
                (math.floor(limit_cad * 1_000_000), time.time(), authorization_reference),
            )
            db.execute(
                "INSERT INTO azure_policy VALUES(1,0) ON CONFLICT(id) DO UPDATE SET disabled=0"
            )

    def reserve(self, kind: str, input_tokens: int = 0, output_tokens: int = 0) -> str:
        s = self.settings
        if kind not in {"reasoning", "embedding", "session_voice", "session_text"}:
            raise ValueError("Unsupported usage category")
        if any(
            not isinstance(v, int) or isinstance(v, bool) or v < 0
            for v in (input_tokens, output_tokens)
        ):
            raise ValueError("Usage bounds must be nonnegative integers")
        if not s.azure_usage_guard_enabled:
            raise AzureUsageBlocked("Azure usage disabled; explicit local enablement required")
        if input_tokens > s.azure_max_input_tokens or output_tokens > s.azure_max_output_tokens:
            raise AzureUsageBlocked("Azure request exceeds the conservative input/output limit")
        if kind == "session_voice":
            minutes = (
                math.ceil(s.azure_setup_max_voice_seconds / 60)
                if s.azure_setup_validation_mode
                else s.azure_max_voice_session_minutes
            )
            cost = minutes * s.azure_estimated_voice_cad_per_minute
        elif kind == "session_text":
            cost = 0
        else:
            rate = (
                s.azure_estimated_embedding_cad_per_million
                if kind == "embedding"
                else s.azure_estimated_input_cad_per_million
            )
            cost = (
                input_tokens * rate + output_tokens * s.azure_estimated_output_cad_per_million
            ) / 1_000_000
        estimate = math.ceil(cost * 1_000_000)
        now = datetime.now(UTC)
        month, day = now.strftime("%Y-%m"), now.strftime("%Y-%m-%d")
        session = kind.startswith("session_")
        # Never allocate a long-lived speech operation across a monthly reset.
        if (
            kind == "session_voice"
            and (now + timedelta(minutes=s.azure_max_voice_session_minutes + 1)).strftime("%Y-%m")
            != month
        ):
            raise AzureUsageBlocked("Start the Azure session after the calendar month resets")
        token = uuid.uuid4().hex
        with self.transaction() as db:
            self._allowed(db)
            spent = db.execute(
                "SELECT COALESCE(SUM(estimate),0) FROM azure_usage WHERE month=?", (month,)
            ).fetchone()[0]
            limit = self.effective_limit(db)
            if spent >= limit or spent + estimate > limit:
                raise AzureUsageBlocked("Estimated monthly Azure application allowance exhausted")
            phase = "setup" if s.azure_setup_validation_mode else "demo"
            if phase == "setup":
                setup = db.execute(
                    "SELECT COALESCE(SUM(estimate),0) FROM azure_usage WHERE phase='setup'"
                ).fetchone()[0]
                if setup + estimate > s.azure_estimated_setup_limit_cad * 1_000_000:
                    raise AzureUsageBlocked("Estimated Azure setup validation allowance exhausted")
            if session:
                active = db.execute(
                    "SELECT COUNT(*) FROM azure_usage WHERE active=1 AND kind LIKE 'session_%'"
                ).fetchone()[0]
                daily = db.execute(
                    "SELECT COUNT(*) FROM azure_usage WHERE day=? AND kind LIKE 'session_%'", (day,)
                ).fetchone()[0]
                if active >= s.azure_max_concurrent_sessions:
                    raise AzureUsageBlocked("Only one live Azure support session may run at a time")
                if daily >= s.azure_max_live_sessions_per_day:
                    raise AzureUsageBlocked("Daily live Azure support session limit reached")
            elif db.execute(
                "SELECT COUNT(*) FROM azure_usage WHERE active=1 "
                "AND kind IN ('reasoning','embedding')"
            ).fetchone()[0]:
                raise AzureUsageBlocked(
                    "Only one Azure reasoning/embedding operation may run at a time"
                )
            db.execute(
                "INSERT INTO azure_usage(id,month,day,kind,estimate,active,phase) "
                "VALUES(?,?,?,?,?,1,?)",
                (token, month, day, kind, estimate, phase),
            )
        return token

    def finish(self, token: str, usage: Any = None, seconds: float = 0) -> None:
        counts = token_counts(usage)
        with self.transaction() as db:
            row = db.execute(
                "SELECT kind,estimate,usage FROM azure_usage WHERE id=?", (token,)
            ).fetchone()
            if not row:
                raise AzureUsageBlocked("Azure usage reservation missing")
            kind, estimate, previous = row
            if (kind == "reasoning" and "input_tokens" in counts and "output_tokens" in counts) or (
                kind == "embedding" and "input_tokens" in counts
            ):
                rate = (
                    self.settings.azure_estimated_embedding_cad_per_million
                    if kind == "embedding"
                    else self.settings.azure_estimated_input_cad_per_million
                )
                estimate = math.ceil(
                    counts.get("input_tokens", 0) * rate
                    + counts.get("output_tokens", 0)
                    * self.settings.azure_estimated_output_cad_per_million
                )
            db.execute(
                "UPDATE azure_usage SET active=0,estimate=?,usage=?,seconds=? WHERE id=?",
                (estimate, json.dumps(counts) if counts else previous, max(0, seconds), token),
            )

    def voice_usage(self, token: str, event_id: str, usage: Any) -> None:
        counts = token_counts(usage)
        if not counts:
            return
        with self.transaction() as db:
            event = hashlib.sha256(event_id.encode()).hexdigest()
            inserted = db.execute(
                "INSERT OR IGNORE INTO azure_voice_usage VALUES(?,?,?)",
                (token, event, json.dumps(counts)),
            ).rowcount
            if inserted:
                row = db.execute("SELECT usage FROM azure_usage WHERE id=?", (token,)).fetchone()
                previous = json.loads(row[0]) if row else {}
                for key, value in counts.items():
                    previous[key] = previous.get(key, 0) + value
                db.execute(
                    "UPDATE azure_usage SET usage=? WHERE id=?", (json.dumps(previous), token)
                )

    def turn(self, token: str) -> int:
        with self.transaction() as db:
            self._allowed(db)
            row = db.execute("SELECT turns,active FROM azure_usage WHERE id=?", (token,)).fetchone()
            if not row or not row[1] or row[0] >= self.settings.azure_max_turns_per_session:
                raise AzureUsageBlocked("Live Azure support turn limit reached")
            db.execute("UPDATE azure_usage SET turns=turns+1 WHERE id=?", (token,))
            return row[0] + 1

    def disable(self) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO azure_policy VALUES(1,1) ON CONFLICT(id) DO UPDATE SET disabled=1"
            )

    def record_check(self, capability: str, passed: bool, code: str = "") -> None:
        if capability not in {
            "auth_reasoning",
            "auth_voice",
            "reasoning",
            "embeddings",
            "mcp",
            "text_workflow",
            "voice",
            "voice_connection",
        }:
            raise ValueError("Unknown validation capability")
        safe_code = code if re.fullmatch(r"[A-Za-z0-9_.-]{0,64}", code) else "REDACTED"
        with self.transaction() as db:
            db.execute(
                "INSERT INTO azure_validation VALUES(?,?,?) "
                "ON CONFLICT(capability) DO UPDATE SET passed=excluded.passed,code=excluded.code",
                (capability, int(passed), safe_code),
            )

    def checks(self) -> dict[str, Any]:
        if not self.ledger_exists():
            return {}
        try:
            with self.inspection() as db:
                return {
                    name: {"passed": bool(passed), "code": code}
                    for name, passed, code in db.execute(
                        "SELECT capability,passed,code FROM azure_validation"
                    )
                }
        except AzureUsageBlocked:
            return {}

    def claim_fixture(self, text: str) -> None:
        digest = hashlib.sha256(text.encode()).hexdigest()
        with self.transaction() as db:
            self._allowed(db)
            row = db.execute("SELECT digest FROM azure_acceptance_fixture WHERE id=1").fetchone()
            if row and row[0] != digest:
                raise AzureUsageBlocked(
                    "Only one fictional knowledge document is allowed in Azure acceptance"
                )
            db.execute("INSERT OR IGNORE INTO azure_acceptance_fixture VALUES(1,?)", (digest,))

    def claim_operator(self, identity: str) -> None:
        digest = hashlib.sha256(identity.encode()).hexdigest()
        with self.transaction() as db:
            self._allowed(db)
            row = db.execute("SELECT digest FROM azure_acceptance_operator WHERE id=1").fetchone()
            if row and row[0] != digest:
                raise AzureUsageBlocked("Azure acceptance is restricted to its first local tester")
            db.execute("INSERT OR IGNORE INTO azure_acceptance_operator VALUES(1,?)", (digest,))

    def snapshot(self) -> dict[str, Any]:
        enabled = (
            self.settings.azure_usage_guard_enabled and not self.settings.azure_emergency_disable
        )
        spent = 0
        usage: dict[str, int] = {}
        error = False
        setup_spent = 0
        limit = self.limit
        if self.ledger_exists():
            try:
                with self.inspection() as db:
                    limit = self.effective_limit(db)
                    disabled = db.execute("SELECT disabled FROM azure_policy WHERE id=1").fetchone()
                    enabled = enabled and not bool(disabled and disabled[0])
                    rows = db.execute(
                        "SELECT estimate,usage FROM azure_usage WHERE month=?",
                        (datetime.now(UTC).strftime("%Y-%m"),),
                    ).fetchall()
                    spent = sum(row[0] for row in rows)
                    setup_spent = db.execute(
                        "SELECT COALESCE(SUM(estimate),0) FROM azure_usage WHERE phase='setup'"
                    ).fetchone()[0]
                    for _, raw in rows:
                        for key, value in json.loads(raw).items():
                            usage[key] = usage.get(key, 0) + value
            except (AzureUsageBlocked, ValueError, TypeError):
                enabled, error = False, True
        remaining = max(0, (limit - spent) / 1_000_000)
        if (
            self.settings.azure_setup_validation_mode
            and setup_spent >= self.settings.azure_estimated_setup_limit_cad * 1_000_000
        ):
            enabled = False
        return {
            "enabled": bool(enabled and remaining > 0),
            "ledger_error": error,
            "currency": "CAD",
            "month": datetime.now(UTC).strftime("%Y-%m"),
            "limit_cad": limit / 1_000_000,
            "estimated_used_cad": spent / 1_000_000,
            "remaining_estimated_cad": remaining,
            "reported_tokens": usage,
            "invoice_cap": False,
            "setup_estimated_used_cad": setup_spent / 1_000_000,
            "setup_limit_cad": self.settings.azure_estimated_setup_limit_cad,
            "setup_mode": self.settings.azure_setup_validation_mode,
        }


def token_counts(usage: Any) -> dict[str, int]:
    """Persist numeric counters only, never provider bodies, prompts or identities."""
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    if not isinstance(usage, dict):
        return {}
    result = {}
    for dest, names in {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "total_tokens": ("total_tokens",),
    }.items():
        for name in names:
            value = usage.get(name)
            if (
                isinstance(value, int)
                and not isinstance(value, bool)
                and 0 <= value <= 1_000_000_000
            ):
                result[dest] = value
                break
    for field in (
        "input_token_details",
        "output_token_details",
        "input_tokens_details",
        "output_tokens_details",
    ):
        details = usage.get(field)
        if isinstance(details, dict):
            for name in ("audio_tokens", "text_tokens", "cached_tokens"):
                value = details.get(name)
                if (
                    isinstance(value, int)
                    and not isinstance(value, bool)
                    and 0 <= value <= 1_000_000_000
                ):
                    result[field + "_" + name] = value
    return result
