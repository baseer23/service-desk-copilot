import time
from types import SimpleNamespace

import jwt
import pytest
from backend.app.core.authentication import authenticate, mutation_origin
from backend.app.core.principal import Principal, sign_principal, verify_principal
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from pydantic import SecretStr
from starlette.requests import Request

TENANT = "11111111-1111-4111-8111-111111111111"
OWNER = "22222222-2222-4222-8222-222222222222"
AUDIENCE = "33333333-3333-4333-8333-333333333333"


def production(settings):
    return settings.model_copy(
        update={
            "deployment_mode": "production",
            "auth_client_id": AUDIENCE,
            "auth_allowed_tenants": [TENANT],
            "auth_operator_ids": [OWNER],
            "voice_session_signing_key": SecretStr("fictional-auth-test-key-xxxxxxxxxxxxxxxxxxx"),
            "allowed_origins": ["https://support.example"],
        }
    )


def request(headers, method="GET"):
    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/cases",
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        }
    )


@pytest.mark.asyncio
async def test_entra_signature_scope_audience_tenant_expiry_and_operator(settings, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        "backend.app.core.authentication.jwks",
        lambda tenant: SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key())
        ),
    )
    claims = {
        "tid": TENANT,
        "oid": OWNER,
        "aud": AUDIENCE,
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "scp": "DeskMate.Access",
    }
    config = production(settings)
    token = jwt.encode(claims, key, algorithm="RS256")
    actor = await authenticate(request({"Authorization": "Bearer " + token}), config)
    assert actor == Principal(TENANT, OWNER, True)
    for overrides in [
        {"aud": "another-service"},
        {"scp": "Other.Access"},
        {"exp": 1},
        {"tid": "untrusted-tenant"},
        {"iss": "https://untrusted.example"},
        {"oid": 42},
    ]:
        invalid = jwt.encode({**claims, **overrides}, key, algorithm="RS256")
        with pytest.raises(HTTPException) as error:
            await authenticate(request({"Authorization": "Bearer " + invalid}), config)
        assert error.value.status_code == 401 and invalid not in str(error.value)
    forged = jwt.encode(
        claims, rsa.generate_private_key(public_exponent=65537, key_size=2048), algorithm="RS256"
    )
    with pytest.raises(HTTPException):
        await authenticate(request({"Authorization": "Bearer " + forged}), config)
    user = jwt.encode(
        {**claims, "oid": "44444444-4444-4444-8444-444444444444"}, key, algorithm="RS256"
    )
    assert not (await authenticate(request({"Authorization": "Bearer " + user}), config)).operator


def test_cookie_cannot_cross_tenants_expire_or_extend_without_fresh_identity(settings):
    config = production(settings)
    token = sign_principal(config, Principal(TENANT, OWNER))
    assert verify_principal(config, token).owner_id == OWNER
    for invalid in [
        token + "tampered",
        sign_principal(config, Principal("another-tenant", OWNER)),
        sign_principal(config, Principal(TENANT, OWNER), 1),
    ]:
        with pytest.raises(PermissionError):
            verify_principal(config, invalid)


def test_cross_origin_mutations_are_rejected(settings):
    config = production(settings)
    mutation_origin(request({"Origin": "https://support.example"}, "POST"), config)
    for headers in [{}, {"Origin": "https://evil.example"}, {"Origin": "null"}]:
        with pytest.raises(HTTPException) as error:
            mutation_origin(request(headers, "POST"), config)
        assert error.value.status_code == 403


def test_public_readiness_hides_operator_details(make_client):
    client = make_client()
    public = client.get("/readiness").json()
    assert isinstance(public["text_ready"], bool)
    assert not {
        "reasoning",
        "voice",
        "embeddings",
        "azure_usage",
        "azure_validation",
        "local_mcp",
        "private_stores",
        "live_azure_validated",
    }.intersection(public)
    assert "azure_usage" not in client.get("/admin/readiness").json()


def test_unresolved_signing_reference_and_unscoped_production_storage_are_rejected(settings):
    from backend.app.core.config import Settings

    values = settings.model_dump()
    values["voice_session_signing_key"] = SecretStr(
        "@Microsoft.KeyVault(SecretUri=https://fictional.vault.azure.net/secrets/signing/)"
    )
    with pytest.raises(ValueError, match="must resolve"):
        Settings(_env_file=None, **values)
    values = settings.model_dump()
    values["deployment_mode"] = "production"
    with pytest.raises(ValueError, match="tenant-scoped persistent local records"):
        Settings(_env_file=None, **values)
    secure = Settings(
        _env_file=None, deployment_mode="production", data_platform="local",
        local_persistence_enabled=True, test_mode=False,
        generation_provider="groq", embed_provider="sentence_transformers",
        embed_model_name="all-MiniLM-L6-v2", enable_local_fixtures=False,
        auth_client_id=AUDIENCE, auth_allowed_tenants=[TENANT], auth_operator_ids=[OWNER],
        voice_session_signing_key=SecretStr("fictional-auth-test-key-xxxxxxxxxxxxxxxxxxx"),
        voice_auth_mode="entra", azure_use_entra_id=True,
    )
    assert secure.uses_record_store and secure.data_platform == "local"


def test_sign_out_stops_only_the_authenticated_owners_speech_recognition(settings):
    import asyncio
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app.core.authentication import register_auth_routes
    from backend.app.speech.api import SpeechService

    config = production(settings)
    app = FastAPI()
    mine, other = asyncio.Event(), asyncio.Event()
    service = SpeechService(config)
    service.active = {
        mine: (Principal(TENANT, OWNER), asyncio.Event()),
        other: (Principal(TENANT, "other-user"), asyncio.Event()),
    }
    app.state.speech = service
    register_auth_routes(app, config)
    client = TestClient(app, base_url="https://support.example")
    client.cookies.set("deskmate_session", sign_principal(config, Principal(TENANT, OWNER)))
    response = client.post("/auth/logout", headers={"Origin": "https://support.example"})
    assert response.status_code == 200 and mine.is_set() and not other.is_set()
    assert "Max-Age=0" in response.headers["set-cookie"]
