"""Entra work-account sign-in and short-lived HttpOnly application sessions."""

import asyncio
import re
import time
from functools import lru_cache

import jwt
from fastapi import HTTPException, Request, Response

from backend.app.core.config import Settings
from backend.app.core.principal import (
    Principal,
    current_principal,
    sign_principal,
    verify_principal,
)

SESSION_COOKIE = "deskmate_session"


@lru_cache(maxsize=16)
def jwks(tenant: str):
    return jwt.PyJWKClient(
        f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys", timeout=5
    )


async def authenticate(request: Request, settings: Settings) -> Principal:
    if settings.deployment_mode != "production":
        return current_principal(settings)
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer "):
        token = header[7:]

        def validate():
            if len(token) > 16384:
                raise ValueError
            tenant = jwt.decode(token, options={"verify_signature": False}).get("tid", "")
            if (
                not re.fullmatch(r"[a-fA-F0-9-]{36}", tenant)
                or tenant not in settings.auth_allowed_tenants
                or not settings.auth_client_id
            ):
                raise ValueError
            key = jwks(tenant).get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=settings.auth_client_id,
                issuer=f"https://login.microsoftonline.com/{tenant}/v2.0",
                options={"require": ["exp", "iat", "tid", "oid"]},
            )
            if claims["tid"] != tenant or "DeskMate.Access" not in claims.get("scp", "").split():
                raise ValueError
            if not isinstance(claims["oid"], str) or not re.fullmatch(
                r"[a-fA-F0-9-]{36}", claims["oid"]
            ):
                raise ValueError
            request.state.identity_expires = int(claims["exp"])
            return Principal(
                tenant,
                claims["oid"],
                claims["oid"] in settings.auth_operator_ids
                or "DeskMate.Operator" in claims.get("roles", []),
                int(claims["exp"]),
            )

        try:
            return await asyncio.wait_for(asyncio.to_thread(validate), 8)
        except Exception:  # noqa: BLE001 - Sanitize SDK/auth failures without disclosing response bodies.
            raise HTTPException(401, "Please sign in with an authorized work account.") from None
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        try:
            return verify_principal(settings, cookie)
        except PermissionError:
            pass
    raise HTTPException(401, "Please sign in to continue.")


def mutation_origin(request: Request, settings: Settings):
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    if (
        settings.deployment_mode == "production"
        and request.headers.get("origin") not in settings.allowed_origins
    ):
        raise HTTPException(403, "This request is not permitted.")


def register_auth_routes(app, settings: Settings):
    @app.get("/auth/configuration")
    async def configuration():
        return {
            "required": settings.deployment_mode == "production",
            "client_id": settings.auth_client_id,
            "authority": "https://login.microsoftonline.com/"
            + (
                settings.auth_allowed_tenants[0]
                if settings.auth_allowed_tenants
                else "organizations"
            ),
            "scope": "api://" + settings.auth_client_id + "/DeskMate.Access",
        }

    @app.get("/auth/me")
    async def me(request: Request):
        actor = await authenticate(request, settings)
        return {"authenticated": True, "operator": actor.operator}

    @app.post("/auth/session")
    async def session(request: Request, response: Response):
        mutation_origin(request, settings)
        if settings.deployment_mode == "production" and not request.headers.get(
            "authorization", ""
        ).startswith("Bearer "):
            raise HTTPException(401, "A new work-account sign-in is required.")
        actor = await authenticate(request, settings)
        expires = min(
            int(time.time()) + 3600,
            getattr(request.state, "identity_expires", int(time.time()) + 3600),
        )
        response.set_cookie(
            SESSION_COOKIE,
            sign_principal(settings, actor, expires),
            httponly=True,
            secure=settings.deployment_mode == "production",
            samesite="strict",
            max_age=max(0, expires - int(time.time())),
        )
        return {"authenticated": True}

    @app.post("/auth/logout")
    async def logout(request: Request, response: Response):
        mutation_origin(request, settings)
        try:
            actor = await authenticate(request, settings)
        except HTTPException:
            actor = None
        service = getattr(request.app.state, "speech", None)
        if actor and service:
            await service.close_for(actor)
        response.delete_cookie(
            SESSION_COOKIE,
            httponly=True,
            secure=settings.deployment_mode == "production",
            samesite="strict",
        )
        from backend.app.voice.auth import COOKIE_NAME

        response.delete_cookie(
            COOKIE_NAME,
            httponly=True,
            secure=settings.deployment_mode == "production",
            samesite="strict",
        )
        return {"signed_out": True}
