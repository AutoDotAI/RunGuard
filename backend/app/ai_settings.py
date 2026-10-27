"""Per-user summary method choice and catalog without credential exposure."""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .ai_credentials import CredentialRepository, CredentialUnavailable
from .db import AiSettingsRecord, UserRepository
from .models import AiModel, AiProvider, AiProviderCatalog, AiSettings, AiSettingsInput
from .provider_config import (DEMO_LABEL, DEMO_MODEL, MODEL_ID,
                              SELF_SERVICE_API_MODELS, configured_litellm_models,
                              has_model_credential)
from .store import Problem


_HOST_TTL = timedelta(seconds=20)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _models(value: str | None, *, include_default: bool = False) -> list[AiModel]:
    ids = (["default"] if include_default else []) + (value or "").split(",")
    result: list[AiModel] = []
    seen: set[str] = set()
    for raw in ids:
        model_id = raw.strip()
        if not MODEL_ID.fullmatch(model_id) or model_id in seen:
            continue
        seen.add(model_id)
        result.append(AiModel(id=model_id, label="CLI default model" if model_id == "default" else model_id))
    return result


class AiSettingsService:
    def __init__(self, users: UserRepository, *,
                 credentials: CredentialRepository | None = None,
                 capability_reader: Callable[[], set[str]] | None = None,
                 environ: dict[str, str] | None = None):
        self.users = users
        self.credentials = credentials or CredentialRepository(users)
        self.capability_reader = capability_reader or self._connected_providers
        self.environ = environ

    def _connected_providers(self) -> set[str]:
        """Only a recent worker heartbeat may advertise runnable providers."""
        try:
            with self.users.sessions() as session:
                row = session.execute(text("SELECT last_seen_at, capabilities_json "
                                           "FROM worker_heartbeat WHERE id = 1")).first()
            if row is None:
                return set()
            timestamp = datetime.fromisoformat(row.last_seen_at.replace("Z", "+00:00"))
            if timestamp.tzinfo is None or _utc_now() - timestamp > _HOST_TTL:
                return set()
            capabilities = json.loads(row.capabilities_json)
            providers = capabilities.get("providers") if isinstance(capabilities, dict) else None
            return {item for item in providers if item in {"extractive", "codex", "claude", "litellm"}} if isinstance(providers, list) else set()
        except (SQLAlchemyError, ValueError, TypeError, AttributeError):
            return set()

    def _credential_ready(self, owner: str, provider: str) -> bool:
        try:
            return self.credentials.is_configured(owner, provider)
        except CredentialUnavailable:
            raise Problem(503, "CREDENTIAL_UNAVAILABLE", "Credential storage is unavailable.") from None

    def _model_key_available(self, owner: str | None, model: str) -> bool:
        for provider, models in SELF_SERVICE_API_MODELS.items():
            if model in models:
                return owner is not None and self._credential_ready(owner, provider)
        environ = self.environ if self.environ is not None else os.environ
        return (model in configured_litellm_models(environ)
                and has_model_credential(model, environ))

    def catalog(self, owner: str | None = None) -> AiProviderCatalog:
        environ = self.environ if self.environ is not None else os.environ
        connected = self.capability_reader()
        codex = _models(environ.get("RUNGUARD_CODEX_MODELS"), include_default=True)
        claude = _models(environ.get("RUNGUARD_CLAUDE_MODELS"), include_default=True)
        self_service_models = [AiModel(id=model, label=model.removeprefix(f"{provider}/"))
                               for provider, models in SELF_SERVICE_API_MODELS.items()
                               for model in models]
        self_service_ids = {item.id for item in self_service_models}
        configured_legacy = [model for model in configured_litellm_models(environ)
                             if model not in self_service_ids]
        credentialed_legacy = [model for model in configured_legacy
                               if has_model_credential(model, environ)]
        legacy_models = credentialed_legacy or configured_legacy
        api_models = self_service_models + [AiModel(id=model, label=model)
                                            for model in legacy_models]
        api_has_key = bool(credentialed_legacy) or (any(
            self._credential_ready(owner, provider) for provider in SELF_SERVICE_API_MODELS)
            if owner else False)
        providers = [AiProvider(provider_path="local_demo", provider="extractive",
                                label=DEMO_LABEL, available="extractive" in connected,
                                unavailable_reason=(None if "extractive" in connected else
                                                    "Local demo worker is not connected."),
                                models=[AiModel(id=DEMO_MODEL, label="Extractive page excerpt")])]
        for provider, label, models in (("codex", "Codex CLI", codex),
                                        ("claude", "Claude CLI", claude)):
            available = provider in connected
            providers.append(AiProvider(provider_path="backend_cli", provider=provider,
                                        label=label, available=available,
                                        unavailable_reason=None if available else f"Host {label.split()[0]} worker is not connected.",
                                        models=models))
        api_available = "litellm" in connected and api_has_key
        api_reason = None
        if not api_available:
            api_reason = ("Add an OpenAI or Anthropic API key."
                          if not api_has_key else "LiteLLM worker is not connected.")
        providers.append(AiProvider(provider_path="api", provider="litellm",
                                    label="API key — OpenAI / Anthropic", available=api_available,
                                    unavailable_reason=api_reason, models=api_models))
        return AiProviderCatalog(providers=providers)

    def get(self, owner: str) -> AiSettings:
        with self.users.sessions() as session:
            row = session.get(AiSettingsRecord, owner)
            selection = (AiSettingsInput(provider_path=row.provider_path, provider=row.provider,
                                         model=row.model) if row else
                         AiSettingsInput(provider_path="local_demo", provider="extractive", model=DEMO_MODEL))
        entry = next((entry for entry in self.catalog(owner).providers
                      if entry.provider_path == selection.provider_path and entry.provider == selection.provider), None)
        available = bool(entry and entry.available and any(model.id == selection.model for model in entry.models))
        if selection.provider_path == "api":
            available = available and self._model_key_available(owner, selection.model)
        return AiSettings(**selection.model_dump(), available=available)

    def put(self, owner: str, selection: AiSettingsInput) -> AiSettings:
        entry = next((entry for entry in self.catalog(owner).providers
                      if entry.provider_path == selection.provider_path and entry.provider == selection.provider), None)
        if entry is None or not any(model.id == selection.model for model in entry.models):
            raise Problem(422, "VALIDATION_ERROR", "Unsupported provider or model.")
        if not entry.available or (selection.provider_path == "api" and
                                   not self._model_key_available(owner, selection.model)):
            raise Problem(409, "PROVIDER_UNAVAILABLE", "Selected provider is unavailable.")
        with self.users.sessions.begin() as session:
            session.execute(text("INSERT INTO ai_settings "
                                 "(owner_user_id, provider_path, provider, model, updated_at) "
                                 "VALUES (:owner, :path, :provider, :model, :updated_at) "
                                 "ON CONFLICT (owner_user_id) DO UPDATE SET "
                                 "provider_path = EXCLUDED.provider_path, "
                                 "provider = EXCLUDED.provider, model = EXCLUDED.model, "
                                 "updated_at = EXCLUDED.updated_at"),
                            {"owner": owner, "path": selection.provider_path,
                             "provider": selection.provider, "model": selection.model,
                             "updated_at": _utc_now().isoformat()})
        return AiSettings(**selection.model_dump(), available=True)
