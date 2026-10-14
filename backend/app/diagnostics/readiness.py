"""Configuration is not authentication or reachability. Never serialize settings values."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.app.core.azure_usage import AzureUsageGuard
from backend.app.core.config import Settings

Check = Literal["YES", "NO", "NOT_CHECKED", "NOT_APPLICABLE"]
StoreCheck = Literal["READY", "ERROR", "NOT_CHECKED"]


class CapabilityReadiness(BaseModel):
    configured: bool
    locally_initialized: bool = False
    authenticated: Check = "NOT_CHECKED"
    reachable: Check = "NOT_CHECKED"
    live_validated: bool = False
    missing: list[str] = Field(default_factory=list)


class ReadinessReport(BaseModel):
    runtime_mode: Literal["isolated_demo", "azure"]
    reasoning: CapabilityReadiness
    embeddings: CapabilityReadiness
    voice: CapabilityReadiness
    local_mcp: StoreCheck = "NOT_CHECKED"
    private_stores: StoreCheck = "NOT_CHECKED"
    session_auth_configured: bool
    text_ready: bool
    voice_ready: bool
    live_azure_validated: bool = False
    azure_usage: dict[str, Any] = Field(default_factory=dict)
    azure_validation: dict[str, Any] = Field(default_factory=dict)
    voice_validation_scope: Literal["not_checked", "configuration", "transport"] = "not_checked"
    local_documents_ready: bool = False
    local_ocr_available: bool = False
    semantic_ingestion_ready: bool = False
    semantic_ingestion_blocker: str | None = None
    url_ingestion_enabled: bool = False


def auth_method(settings: Settings, voice: bool = False) -> Literal["ENTRA", "API_KEY", "MISSING"]:
    if settings.azure_use_entra_id:
        return "ENTRA"
    key = settings.azure_voicelive_api_key if voice else settings.azure_openai_api_key
    return "API_KEY" if key else "MISSING"


def configuration_readiness(
    settings: Settings,
    *,
    reasoning_initialized: bool = False,
    local_mcp: Literal["READY", "ERROR", "NOT_CHECKED"] = "NOT_CHECKED",
    private_stores: Literal["READY", "ERROR", "NOT_CHECKED"] = "NOT_CHECKED",
) -> ReadinessReport:
    demo = (
        settings.test_mode
        and settings.generation_provider == "stub"
        and settings.embed_provider == "stub"
        and settings.voice_provider == "fake"
    )
    allowance = AzureUsageGuard(settings).snapshot()
    reasoning_missing = (
        []
        if demo
        else [
            key
            for key, value in {
                "AZURE_FOUNDRY_ENDPOINT": settings.azure_foundry_endpoint,
                "AZURE_MODEL_DEPLOYMENT": settings.azure_model_deployment,
                "AZURE_OPENAI_API_VERSION": settings.azure_openai_api_version,
                "AZURE_REASONING_AUTHENTICATION": auth_method(settings) != "MISSING",
            }.items()
            if not value
        ]
    )
    embedding_missing = (
        []
        if demo
        else [
            key
            for key, value in {
                "AZURE_FOUNDRY_ENDPOINT": settings.azure_foundry_endpoint,
                "AZURE_EMBEDDING_DEPLOYMENT": settings.azure_embedding_deployment,
                "EMBED_MODEL_NAME": settings.embed_model_name,
                "AZURE_EMBEDDING_AUTHENTICATION": auth_method(settings) != "MISSING",
            }.items()
            if not value
        ]
    )
    voice_missing = [
        key
        for key, value in {
            "VOICE_ENABLED": settings.voice_enabled,
            "AZURE_VOICELIVE_ENDPOINT": demo or settings.azure_voicelive_endpoint,
            "AZURE_VOICELIVE_MODEL": demo or settings.azure_voicelive_model,
            "AZURE_VOICELIVE_API_VERSION": demo or settings.azure_voicelive_api_version,
            "AZURE_VOICELIVE_VOICE": demo or settings.azure_voicelive_voice,
            "AZURE_VOICELIVE_AUTHENTICATION": demo or auth_method(settings, True) != "MISSING",
        }.items()
        if not value
    ]
    if not demo and not allowance["enabled"]:
        blocker = (
            "AZURE_USAGE_LEDGER" if allowance["ledger_error"] else "AZURE_USAGE_GUARD_OR_ALLOWANCE"
        )
        reasoning_missing.append(blocker)
        embedding_missing.append(blocker)
        voice_missing.append(blocker)
    session_auth = settings.voice_auth_mode == "local_demo" or bool(
        settings.voice_auth_tenant_id
        and settings.voice_auth_audience
        and settings.voice_session_signing_key
    )
    local_ready = local_mcp == "READY" and private_stores == "READY"
    from backend.app.services.document_extract import ocr_available

    indexing_blocker = None
    if not demo and settings.azure_live_test_mode:
        indexing_blocker = (
            "The Azure acceptance policy only permits its original tiny pasted fixture. "
            "Save documents locally and use keyword search."
        )
    elif embedding_missing:
        indexing_blocker = (
            "Azure embeddings are unavailable or disabled by the usage guard. "
            "Local extraction and keyword search remain available."
        )
    elif not local_ready:
        indexing_blocker = "Local MCP or knowledge storage is unavailable."
    reason = CapabilityReadiness(
        configured=not reasoning_missing,
        locally_initialized=reasoning_initialized,
        missing=reasoning_missing,
    )
    embedding = CapabilityReadiness(configured=not embedding_missing, missing=embedding_missing)
    voice = CapabilityReadiness(configured=not voice_missing, missing=voice_missing)
    checks = {} if demo else AzureUsageGuard(settings).checks()
    for name, cap in (("reasoning", reason), ("embeddings", embedding), ("voice", voice)):
        proof = checks.get(name)
        if proof:
            cap.live_validated = proof["passed"]
            cap.reachable = "YES" if proof["passed"] else "NO"
            cap.authenticated = "YES" if proof["passed"] else "NOT_CHECKED"
            cap.locally_initialized = cap.locally_initialized or proof["passed"]
    if checks.get("voice_connection", {}).get("passed"):
        voice.authenticated = "YES"
        voice.reachable = "YES"
        voice.locally_initialized = True
    if demo:
        for capability in (reason, embedding, voice):
            capability.authenticated = "NOT_APPLICABLE"
            capability.reachable = "NOT_APPLICABLE"
    return ReadinessReport(
        runtime_mode="isolated_demo" if demo else "azure",
        azure_usage=allowance,
        azure_validation=checks,
        local_documents_ready=local_mcp == "READY",
        local_ocr_available=ocr_available(),
        semantic_ingestion_ready=indexing_blocker is None,
        semantic_ingestion_blocker=indexing_blocker,
        url_ingestion_enabled=settings.allow_url_ingest and not settings.azure_live_test_mode,
        voice_validation_scope=(
            "transport"
            if checks.get("voice", {}).get("passed")
            else (
                "configuration"
                if checks.get("voice_connection", {}).get("passed")
                else "not_checked"
            )
        ),
        live_azure_validated=all(cap.live_validated for cap in (reason, embedding, voice)),
        reasoning=reason,
        embeddings=embedding,
        voice=voice,
        local_mcp=local_mcp,
        private_stores=private_stores,
        session_auth_configured=session_auth,
        text_ready=reason.configured
        and reasoning_initialized
        and local_ready
        and session_auth,
        voice_ready=reason.configured
        and (demo or allowance.get("remaining_estimated_cad", 0) >= (
            settings.azure_max_voice_session_minutes * settings.azure_estimated_voice_cad_per_minute
        ))
        and reason.locally_initialized
        and embedding.configured
        and voice.configured
        and local_ready
        and session_auth,
    )
