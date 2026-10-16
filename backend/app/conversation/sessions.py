from __future__ import annotations
import secrets, time
from threading import RLock
from backend.app.conversation.models import ConversationSession


class ConversationSessions:
    def __init__(self, ttl_seconds=1800):
        self.ttl_seconds = ttl_seconds
        self._items = {}
        self._lock = RLock()

    def create(self):
        with self._lock:
            now = time.monotonic()
            self._items = {
                k: v for k, v in self._items.items() if now - v.touched_at < self.ttl_seconds
            }
            value = ConversationSession(secrets.token_urlsafe(18))
            self._items[value.session_id] = value
            return value

    def get(self, session_id):
        with self._lock:
            value = self._items.get(session_id)
            if value and time.monotonic() - value.touched_at < self.ttl_seconds:
                value.touched_at = time.monotonic()
                return value
            self._items.pop(session_id, None)
            return None
