"""Signed HttpOnly browser identity; Azure keys never cross this boundary."""

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass

from backend.app.core.config import Settings

COOKIE_NAME = "deskmate_voice_identity"


class VoiceError(ValueError):
    def __init__(
        self, message: str, status_code: int = 409, code: str = "VOICE_SESSION_ERROR"
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class Actor:
    owner_id: str
    tenant_id: str


def issue_actor(settings: Settings, owner_id: str | None = None) -> str:
    if not settings.voice_session_signing_key:
        raise VoiceError("Voice session authentication is unavailable", 503)
    payload = {
        "uid": owner_id or uuid.uuid4().hex,
        "tid": settings.voice_tenant_id,
        "exp": int(time.time()) + 86400,
    }
    encoded = (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(
        settings.voice_session_signing_key.get_secret_value().encode(),
        encoded.encode(),
        hashlib.sha256,
    ).hexdigest()
    return encoded + "." + signature


def verify_actor(settings: Settings, token: str | None) -> Actor:
    try:
        if not token or len(token) > 2048 or not settings.voice_session_signing_key:
            raise ValueError("No identity")
        encoded, signature = token.split(".")
        expected = hmac.new(
            settings.voice_session_signing_key.get_secret_value().encode(),
            encoded.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("Invalid identity")
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if (
            payload["exp"] < time.time()
            or payload["tid"] != settings.voice_tenant_id
            or not isinstance(payload["uid"], str)
        ):
            raise ValueError("Expired identity")
        return Actor(payload["uid"], payload["tid"])
    except Exception:  # noqa: BLE001 - no token or claim disclosure
        raise VoiceError("Voice session authorization is required", 401) from None
