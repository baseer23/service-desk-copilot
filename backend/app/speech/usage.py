"""In-memory connected-time telemetry for Azure Speech recognition."""

import time


class SpeechMeter:
    """Track connected recognition time without reservations or cost enforcement."""

    def __init__(self, settings, guard=None, clock=time.monotonic):
        self.clock = clock
        self.started = None
        self.connected_seconds = 0.0
        self.connected_at = None
        self.finished = False

    def start(self):
        self.started = self.clock()

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
        return None

    def finish(self, *, confirmed_closed):
        if self.finished or self.started is None:
            return
        self.disconnected()
        self.finished = True

    def snapshot(self):
        return {"connected_seconds": round(self.seconds, 1)}
