"""Authenticated AI provider catalog and per-account choice."""

from fastapi import APIRouter, Depends, Request

from ..auth import current_user
from ..models import AiProviderCatalog, AiSettings, AiSettingsInput


router = APIRouter(prefix="/api/v1/ai", tags=["AI Settings"])


@router.get("/providers", response_model=AiProviderCatalog)
def list_providers(request: Request, _owner: str = Depends(current_user)):
    return request.app.state.ai_settings.catalog()


@router.get("/settings", response_model=AiSettings)
def get_settings(request: Request, owner: str = Depends(current_user)):
    return request.app.state.ai_settings.get(owner)


@router.put("/settings", response_model=AiSettings)
def put_settings(payload: AiSettingsInput, request: Request, owner: str = Depends(current_user)):
    return request.app.state.ai_settings.put(owner, payload)
