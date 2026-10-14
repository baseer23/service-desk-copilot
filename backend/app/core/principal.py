"""Validated tenant authority, captured per MCP call rather than model arguments."""

import base64
import hashlib
import hmac
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from backend.app.core.config import Settings


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    owner_id: str
    operator: bool = False
    expires_at: int | None = field(default=None, compare=False)


_principal: ContextVar[Principal | None] = ContextVar("deskmate_principal", default=None)


def current_principal(settings: Settings) -> Principal:
    actor = _principal.get()
    if actor:
        if actor.expires_at is not None and actor.expires_at <= time.time():
            raise PermissionError("Session authorization expired")
        return actor
    if settings.deployment_mode == "production":
        raise PermissionError("Authenticated tenant authority is required")
    tenant = (
        settings.auth_allowed_tenants[0]
        if settings.auth_allowed_tenants
        else settings.voice_tenant_id
    )
    return Principal(tenant, "local-operator", True)


@contextmanager
def principal_scope(principal: Principal):
    token = _principal.set(principal)
    try:
        yield principal
    finally:
        _principal.reset(token)


def sign_principal(settings: Settings, principal: Principal, expires: int | None = None) -> str:
    if not settings.voice_session_signing_key:
        raise PermissionError("Session signing is unavailable")
    data = json.dumps(
        {
            "tid": principal.tenant_id,
            "oid": principal.owner_id,
            "operator": principal.operator,
            "exp": min(
                expires or int(time.time()) + 300, principal.expires_at or int(time.time()) + 3600
            ),
        },
        separators=(",", ":"),
    ).encode()
    payload = base64.urlsafe_b64encode(data).decode().rstrip("=")
    signature = hmac.new(
        settings.voice_session_signing_key.get_secret_value().encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    return payload + "." + signature


def verify_principal(settings: Settings, token: str) -> Principal:
    try:
        if len(token) > 4096 or not settings.voice_session_signing_key:
            raise ValueError
        payload, signature = token.split(".")
        expected = hmac.new(
            settings.voice_session_signing_key.get_secret_value().encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        value = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if (
            type(value["exp"]) is not int
            or value["exp"] <= time.time()
            or value["exp"] > time.time() + 3600
            or not isinstance(value["tid"], str)
            or not value["tid"]
            or not isinstance(value["oid"], str)
            or not value["oid"]
            or type(value.get("operator")) is not bool
        ):
            raise ValueError
        if (
            settings.deployment_mode == "production"
            and value["tid"] not in settings.auth_allowed_tenants
        ):
            raise ValueError
        return Principal(value["tid"], value["oid"], value.get("operator") is True, value["exp"])
    except Exception:  # noqa: BLE001 - Sanitize SDK/auth failures without disclosing response bodies.
        raise PermissionError("Session authorization failed") from None
