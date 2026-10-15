"""Side-effect-free capability configuration; no credentials or network probes."""

from typing import Literal
from pydantic import BaseModel, Field
from backend.app.core.config import Settings
from backend.app.models.provider_routing import SelectableReasoningProvider, selected_provider

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
    runtime_mode: Literal["isolated_demo", "local"]
    reasoning: CapabilityReadiness
    embeddings: CapabilityReadiness
    voice: CapabilityReadiness
    local_mcp: StoreCheck = "NOT_CHECKED"
    private_stores: StoreCheck = "NOT_CHECKED"
    session_auth_configured: bool
    text_ready: bool
    voice_ready: bool
    live_azure_validated: bool = False
    voice_validation_scope: Literal["not_checked", "configuration", "transport"] = "not_checked"
    local_documents_ready: bool = False
    local_ocr_available: bool = False
    semantic_ingestion_ready: bool = False
    semantic_ingestion_blocker: str | None = None
    url_ingestion_enabled: bool = False
    selected_provider: str = "groq"


def auth_method(settings: Settings, voice: bool = False) -> Literal["ENTRA", "API_KEY", "MISSING"]:
    if voice or settings.azure_use_entra_id:
        return "ENTRA"
    return "API_KEY" if settings.azure_openai_api_key else "MISSING"


def configuration_readiness(settings: Settings, *, reasoning_initialized=False, local_mcp: StoreCheck="NOT_CHECKED", private_stores: StoreCheck="NOT_CHECKED") -> ReadinessReport:
    from backend.app.services.document_extract import ocr_available
    from backend.app.speech.api import speech_readiness

    selection = selected_provider(settings)
    demo = settings.test_mode and selection == "stub"
    configuration = SelectableReasoningProvider(settings).configuration()
    missing = list(configuration["missing"])
    embedding_missing = []
    if settings.embed_provider == "stub" and not settings.test_mode:
        embedding_missing.append("TEST_MODE")
    if not settings.embed_model_name:
        embedding_missing.append("EMBED_MODEL_NAME")
    speech = speech_readiness(settings)
    speech_missing = []
    if not settings.speech_enabled:
        speech_missing.append("SPEECH_ENABLED")
    if not settings.azure_speech_endpoint:
        speech_missing.append("AZURE_SPEECH_ENDPOINT")
    stores = local_mcp == "READY" and private_stores == "READY"
    auth = settings.deployment_mode == "local" or bool(settings.auth_client_id and settings.auth_allowed_tenants and settings.voice_session_signing_key)
    reason = CapabilityReadiness(configured=not missing, locally_initialized=reasoning_initialized, missing=missing)
    vectors = CapabilityReadiness(configured=not embedding_missing, locally_initialized=stores and not embedding_missing, authenticated="NOT_APPLICABLE", reachable="NOT_APPLICABLE", missing=embedding_missing)
    voice = CapabilityReadiness(configured=speech["configured"], missing=speech_missing)
    return ReadinessReport(runtime_mode="isolated_demo" if demo else "local", reasoning=reason, embeddings=vectors, voice=voice,
        selected_provider=selection, local_mcp=local_mcp, private_stores=private_stores,
        session_auth_configured=auth, text_ready=not missing and reasoning_initialized and stores and auth,
        voice_ready=speech["ready"] and auth,
        local_documents_ready=local_mcp=="READY", local_ocr_available=ocr_available(),
        semantic_ingestion_ready=stores and not embedding_missing,
        semantic_ingestion_blocker=None if stores and not embedding_missing else "Local knowledge indexing is unavailable.",
        url_ingestion_enabled=settings.allow_url_ingest)
