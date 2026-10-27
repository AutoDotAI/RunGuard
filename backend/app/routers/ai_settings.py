"""Authenticated AI provider catalog and per-account choice."""

from fastapi import APIRouter, Depends, Request

from ..ai_credentials import CredentialUnavailable
from ..auth import current_user
from ..models import (AiCredentialStatuses, AiCredentialWrite, AiCredentialStatus,
                      AiProviderCatalog, AiSettings, AiSettingsInput)
from ..store import Problem


router = APIRouter(prefix="/api/v1/ai", tags=["AI Settings"])


@router.get("/providers", response_model=AiProviderCatalog)
def list_providers(request: Request, owner: str = Depends(current_user)):
    return request.app.state.ai_settings.catalog(owner)


@router.get("/settings", response_model=AiSettings)
def get_settings(request: Request, owner: str = Depends(current_user)):
    return request.app.state.ai_settings.get(owner)


@router.put("/settings", response_model=AiSettings)
def put_settings(payload: AiSettingsInput, request: Request, owner: str = Depends(current_user)):
    return request.app.state.ai_settings.put(owner, payload)


def _credential_provider(provider: str) -> str:
    if provider not in ("openai", "anthropic"):
        raise Problem(422, "VALIDATION_ERROR", "Unsupported API provider.")
    return provider


@router.get("/credentials", response_model=AiCredentialStatuses)
def list_credentials(request: Request, owner: str = Depends(current_user)):
    try:
        configured = request.app.state.ai_credentials.configured(owner)
    except CredentialUnavailable:
        raise Problem(503, "CREDENTIAL_UNAVAILABLE", "Credential storage is unavailable.") from None
    return AiCredentialStatuses(credentials=[
        AiCredentialStatus(provider=provider, configured=configured[provider])
        for provider in ("openai", "anthropic")])


@router.put("/credentials/{provider}", response_model=AiCredentialStatus)
def put_credential(provider: str, payload: AiCredentialWrite, request: Request,
                   owner: str = Depends(current_user)):
    provider = _credential_provider(provider)
    try:
        request.app.state.ai_credentials.put(owner, provider, payload.api_key.get_secret_value())
    except ValueError:
        raise Problem(422, "VALIDATION_ERROR", "Invalid API key.") from None
    except CredentialUnavailable:
        raise Problem(503, "CREDENTIAL_UNAVAILABLE", "Credential storage is unavailable.") from None
    return AiCredentialStatus(provider=provider, configured=True)


@router.delete("/credentials/{provider}", response_model=AiCredentialStatus)
def delete_credential(provider: str, request: Request, owner: str = Depends(current_user)):
    provider = _credential_provider(provider)
    try:
        request.app.state.ai_credentials.delete(owner, provider)
    except CredentialUnavailable:
        raise Problem(503, "CREDENTIAL_UNAVAILABLE", "Credential storage is unavailable.") from None
    return AiCredentialStatus(provider=provider, configured=False)
