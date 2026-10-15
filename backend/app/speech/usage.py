"""Connected recognition time, including silence, in the existing CAD ledger."""

import math
import time
import uuid
from datetime import UTC, datetime

from backend.app.core.azure_usage import AzureUsageBlocked, AzureUsageGuard


class SpeechMeter:
    """Reserve ahead in minute increments; settle only a confirmed SDK stop.

    Historical Voice Live reservations are never changed. A process crash retains
    its final reservation and active flag for operator review. This estimate is
    deliberately charged even when a resource may have unverified F0 allowance.
    """

    def __init__(self, settings, guard=None, clock=time.monotonic):
        self.settings = settings
        self.guard = guard or AzureUsageGuard(settings)
        self.clock = clock
        self.id = uuid.uuid4().hex
        self.started = None
        self.reserved_seconds = 0
        self.connected_seconds = 0.0
        self.connected_at = None
        self.finished = False
        self.month = datetime.now(UTC).strftime("%Y-%m")
        self.phase = "setup" if settings.azure_setup_validation_mode else "demo"

    def amount(self, seconds):
        return math.ceil(
            seconds * self.settings.azure_speech_estimated_cad_per_hour / 3600 * 1_000_000
        )

    def start(self):
        now = datetime.now(UTC)
        with self.guard.transaction() as db:
            self.guard._allowed(db)
            active = db.execute(
                "SELECT COUNT(*) FROM azure_usage WHERE active=1 AND kind='speech_stt'"
            ).fetchone()[0]
            if active >= self.settings.speech_max_concurrent_sessions:
                raise AzureUsageBlocked("Another speech recognition session is still active")
            self._reserve(db, 60, initial=True, now=now)
        self.started = self.clock()

    def _reserve(self, db, seconds, *, initial=False, now=None):
        now = now or datetime.now(UTC)
        if now.strftime("%Y-%m") != self.month:
            raise AzureUsageBlocked("Restart recognition after the calendar month changes")
        previous = self.amount(self.reserved_seconds)
        estimate = self.amount(seconds)
        spent = db.execute(
            "SELECT COALESCE(SUM(estimate),0) FROM azure_usage WHERE month=?", (self.month,)
        ).fetchone()[0]
        if spent + estimate - previous > self.guard.effective_limit(db):
            raise AzureUsageBlocked("The estimated speech allowance is exhausted")
        if self.phase == "setup":
            setup = db.execute(
                "SELECT COALESCE(SUM(estimate),0) FROM azure_usage WHERE phase='setup'"
            ).fetchone()[0]
            if (
                setup + estimate - previous
                > self.settings.azure_estimated_setup_limit_cad * 1_000_000
            ):
                raise AzureUsageBlocked("The estimated setup allowance is exhausted")
        if initial:
            db.execute(
                "INSERT INTO azure_usage(id,month,day,kind,estimate,active,phase) "
                "VALUES(?,?,?,?,?,1,?)",
                (self.id, self.month, now.strftime("%Y-%m-%d"), "speech_stt", estimate, self.phase),
            )
        else:
            db.execute(
                "UPDATE azure_usage SET estimate=? WHERE id=? AND active=1", (estimate, self.id)
            )
        self.reserved_seconds = seconds

    def connected(self):
        if self.connected_at is None:
            self.connected_at = self.clock()

    def disconnected(self):
        if self.connected_at is not None:
            self.connected_seconds += max(0, self.clock() - self.connected_at)
            self.connected_at = None

    @property
    def seconds(self):
        return self.connected_seconds + (
            max(0, self.clock() - self.connected_at) if self.connected_at is not None else 0
        )

    def heartbeat(self):
        self.guard.require_enabled()
        # Reserve five seconds before the next connected minute. Silence and mute
        # retain the live stream and count; reconnect downtime does not.
        if self.seconds >= self.reserved_seconds - 5:
            with self.guard.transaction() as db:
                self.guard._allowed(db)
                self._reserve(db, self.reserved_seconds + 60)

    def finish(self, *, confirmed_closed):
        if self.finished or self.started is None:
            return
        self.disconnected()
        with self.guard.transaction() as db:
            if confirmed_closed:
                db.execute(
                    "UPDATE azure_usage SET active=0,estimate=?,seconds=? WHERE id=?",
                    (self.amount(self.seconds), self.seconds, self.id),
                )
            else:
                db.execute("UPDATE azure_usage SET seconds=? WHERE id=?", (self.seconds, self.id))
        self.finished = True

    def snapshot(self):
        return {
            "connected_seconds": round(self.seconds, 1),
            "estimated_cad": round(
                self.seconds * self.settings.azure_speech_estimated_cad_per_hour / 3600, 6
            ),
            "estimate_only": True,
        }
