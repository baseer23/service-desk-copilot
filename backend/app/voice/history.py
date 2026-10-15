"""Read-only access to previously saved owner-scoped voice conversations.

The former generated-audio runtime is retired. Historical case links remain usable.
"""

import asyncio
import sqlite3

from backend.app.speech.api import browser_guard as speech_browser_guard
from .auth import VoiceError


def browser_guard(request, settings):
    # Historical GET links may omit Origin; mutation/speech uses the stricter guard.
    if request.method == "GET" and not request.headers.get("origin"):
        return
    try:
        speech_browser_guard(request, settings)
    except Exception:
        raise VoiceError("This connection is not permitted", 403) from None


class VoiceHistory:
    def __init__(self, settings):
        self.settings = settings
        self.repository = self

    async def store(self, function, *args):
        return await asyncio.to_thread(function, *args)

    def get(self, session_id, actor):
        s = self.settings
        if s.uses_record_store:
            from backend.app.store.local_records import record_store
            records = record_store(s)
            try:
                row = records.read("voice", session_id, tenant=actor.tenant_id)
                value = row["payload"].get("state") if row and row.get("owner_id") == actor.owner_id else None
            finally:
                records.close()
        else:
            with sqlite3.connect(s.case_db_path) as db:
                try:
                    row = db.execute("SELECT state_json FROM voice_sessions WHERE session_id=? AND owner_id=? AND tenant_id=?", (session_id, actor.owner_id, actor.tenant_id)).fetchone()
                except sqlite3.OperationalError:
                    row = None
            import json
            value = json.loads(row[0]) if row else None
        if not value or value.get("owner_id") != actor.owner_id or value.get("tenant_id") != actor.tenant_id:
            raise VoiceError("Conversation not found", 404)
        return value
