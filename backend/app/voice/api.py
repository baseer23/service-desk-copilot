"""Authenticated same-origin browser sessions; server-only Azure credentials."""

import asyncio
import ipaddress
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket

from backend.app.core.config import Settings

from .auth import COOKIE_NAME, Actor, VoiceError, issue_actor, verify_actor
from .models import SessionStart, VoiceConversation


def browser_guard(request: Request | WebSocket, settings: Settings) -> None:
    origin = request.headers.get("origin")
    host = request.headers.get("host", "")
    scheme = "https" if request.url.scheme in {"https", "wss"} else "http"
    same_origin = bool(
        origin and urlsplit(origin).netloc == host and urlsplit(origin).scheme == scheme
    )
    if origin and origin not in settings.allowed_origins and not same_origin:
        raise VoiceError("Voice origin is not permitted", 403)
    if settings.voice_auth_mode == "local_demo":
        client = request.client.host if request.client else ""
        try:
            loopback = ipaddress.ip_address(client).is_loopback
        except ValueError:
            loopback = client == "testclient" and settings.test_mode
        hostname = host.split(":")[0]
        if not loopback or hostname not in {"localhost", "127.0.0.1", "[", "testserver"}:
            raise VoiceError("Local demo voice identity requires loopback access", 403)
    # WebSockets and state-changing requests require a browser Origin. Same-origin
    # GET inspection may omit it. TestClient explicitly supplies Origin in tests.
    if not origin and (isinstance(request, WebSocket) or getattr(request, "method", "") == "POST"):
        raise VoiceError("Browser Origin is required", 403)


async def actor_for(
    request: Request | WebSocket, settings: Settings, *, create: bool = False
) -> tuple[Actor, str]:
    browser_guard(request, settings)
    token = request.cookies.get(COOKIE_NAME)
    if token:
        try:
            return verify_actor(settings, token), token
        except VoiceError:
            # Fresh isolated launches rotate the signing key. Only an explicit
            # new loopback demo session may replace stale identity; existing
            # sessions/resumes still require their original valid owner cookie.
            if not create or settings.voice_auth_mode != "local_demo":
                raise
    if not create:
        raise VoiceError("Voice session authorization is required", 401)
    if settings.voice_auth_mode == "entra":
        authorization = request.headers.get("authorization", "")
        if (
            not authorization.startswith("Bearer ")
            or not settings.voice_auth_tenant_id
            or not settings.voice_auth_audience
        ):
            raise VoiceError("Microsoft Entra sign-in is required", 401)
        tenant = settings.voice_auth_tenant_id
        import re

        if not re.fullmatch(r"[a-fA-F0-9-]{36}", tenant):
            raise VoiceError("Voice identity is not configured", 503)

        def validate() -> dict[str, Any]:
            import jwt

            jwks = jwt.PyJWKClient(
                "https://login.microsoftonline.com/" + tenant + "/discovery/v2.0/keys", timeout=5
            )
            access = authorization[7:]
            if len(access) > 16384:
                raise ValueError("Oversized token")
            key = jwks.get_signing_key_from_jwt(access)
            return jwt.decode(
                access,
                key.key,
                algorithms=["RS256"],
                audience=settings.voice_auth_audience,
                issuer="https://login.microsoftonline.com/" + tenant + "/v2.0",
                options={"require": ["exp", "iat", "tid", "oid"]},
            )

        try:
            claims = await asyncio.wait_for(asyncio.to_thread(validate), 8)
            if claims["tid"] != tenant:
                raise ValueError("Wrong tenant")
            token = issue_actor(settings, claims["oid"])
        except Exception:  # noqa: BLE001 - sanitize external failures at the boundary
            raise VoiceError("Microsoft Entra authorization failed", 401) from None
    else:
        token = issue_actor(settings)
    return verify_actor(settings, token), token


def register_voice_routes(app: FastAPI) -> None:
    def manager() -> Any:
        return app.state.voice

    @app.post("/azure/usage/disable")
    async def disable_azure(request: Request) -> dict[str, Any]:
        service = manager()
        if service.settings.deployment_mode != "local":
            raise VoiceError("Use the host configuration to disable Azure", 403)
        # This subscription-wide local switch is only exposed to a loopback operator.
        import ipaddress

        try:
            loopback = bool(
                request.client and ipaddress.ip_address(request.client.host).is_loopback
            )
        except ValueError:
            loopback = (
                request.client is not None
                and request.client.host == "testclient"
                and service.settings.test_mode
            )
        if not loopback:
            raise VoiceError("Azure emergency control requires loopback access", 403)
        await actor_for(request, service.settings, create=True)
        service.usage_guard.disable()
        await service.close()
        return {"azure_usage": service.usage_guard.snapshot()}

    @app.exception_handler(VoiceError)
    async def voice_error(request: Request, exc: VoiceError) -> Any:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=exc.status_code, content={"detail": str(exc), "code": exc.code}
        )

    @app.post("/voice/sessions", status_code=201)
    async def start(body: SessionStart, request: Request, response: Response) -> dict[str, Any]:
        service = manager()
        service.ready(body.mode)
        actor, token = await actor_for(
            request, service.settings, create=not bool(body.resume_session_id)
        )
        state = (
            service.repository.get(body.resume_session_id, actor)
            if body.resume_session_id
            else service.create(actor, body.mode)
        )
        if body.resume_session_id and (
            state.status.value == "completed" or state.failure_category == "explicit_stop"
        ):
            raise VoiceError(
                "This session has stopped. Start a new session.", 409, "VOICE_SESSION_TERMINAL"
            )
        if body.resume_session_id:

            def switch_mode(conversation: VoiceConversation) -> None:
                if conversation.status.value not in {"stopped", "failed"}:
                    raise VoiceError(
                        "The previous connection is still active. Wait before reconnecting.",
                        409,
                        "VOICE_SESSION_ACTIVE",
                    )
                conversation.mode = body.mode

            state = service.repository.update(state.session_id, actor, switch_mode)
        response.set_cookie(
            COOKIE_NAME,
            token,
            httponly=True,
            samesite="strict",
            secure=service.settings.deployment_mode == "production"
            or request.url.scheme == "https",
            max_age=86400,
            path="/",
        )
        return state.public()

    @app.get("/voice/sessions/{session_id}")
    async def get(session_id: str, request: Request) -> dict[str, Any]:
        actor, _ = await actor_for(request, manager().settings)
        return manager().repository.get(session_id, actor).public()

    @app.post("/voice/sessions/{session_id}/stop")
    async def stop(session_id: str, request: Request) -> dict[str, Any]:
        actor, _ = await actor_for(request, manager().settings)
        return manager().end(session_id, actor).public()

    @app.post("/voice/sessions/{session_id}/create-case")
    async def create_case(session_id: str, request: Request) -> dict[str, Any]:
        actor, token = await actor_for(request, manager().settings)
        manager().repository.get(session_id, actor)
        result = await manager().harness.invoke(
            "create_case_from_voice_session", {"session_id": session_id, "identity_token": token}
        )
        if result.get("status") != "ok":
            raise HTTPException(
                409, "Case summary could not be saved; confirm all contact fields first"
            )
        return result["result"]

    @app.websocket("/voice/sessions/{session_id}/stream")
    async def stream(socket: WebSocket, session_id: str) -> None:
        try:
            actor, _ = await actor_for(socket, manager().settings)
            await manager().connect(socket, session_id, actor)
        except VoiceError as exc:
            await socket.close(code=1008, reason=exc.code)
