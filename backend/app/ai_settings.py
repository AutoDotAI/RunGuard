"""Per-user summary method choice and catalog without credential exposure."""

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .db import AiSettingsRecord, UserRepository
from .models import AiModel, AiProvider, AiProviderCatalog, AiSettings, AiSettingsInput
from .provider_config import (DEMO_LABEL, DEMO_MODEL, MODEL_ID,
                              configured_litellm_models, has_model_credential)
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
                 capability_reader: Callable[[], set[str]] | None = None,
                 environ: dict[str, str] | None = None):
        self.users = users
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

    def catalog(self) -> AiProviderCatalog:
        environ = self.environ if self.environ is not None else os.environ
        connected = self.capability_reader()
        codex = _models(environ.get("RUNGUARD_CODEX_MODELS"), include_default=True)
        claude = _models(environ.get("RUNGUARD_CLAUDE_MODELS"), include_default=True)
        configured_api_models = [AiModel(id=model, label=model)
                                 for model in configured_litellm_models(environ)]
        credentialed_api_models = [item for item in configured_api_models
                                   if has_model_credential(item.id, environ)]
        # The schema has availability at provider level, so do not advertise
        # a model that this server cannot authenticate while another works.
        api_models = credentialed_api_models or configured_api_models
        api_has_key = bool(credentialed_api_models)
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
            api_reason = ("No server-side model credential is configured."
                          if not api_has_key else "LiteLLM worker is not connected.")
        providers.append(AiProvider(provider_path="api", provider="litellm",
                                    label="LiteLLM API", available=api_available,
                                    unavailable_reason=api_reason, models=api_models))
        return AiProviderCatalog(providers=providers)

    def get(self, owner: str) -> AiSettings:
        with self.users.sessions() as session:
            row = session.get(AiSettingsRecord, owner)
            selection = (AiSettingsInput(provider_path=row.provider_path, provider=row.provider,
                                         model=row.model) if row else
                         AiSettingsInput(provider_path="local_demo", provider="extractive", model=DEMO_MODEL))
        entry = next((entry for entry in self.catalog().providers
                      if entry.provider_path == selection.provider_path and entry.provider == selection.provider), None)
        available = bool(entry and entry.available and any(model.id == selection.model for model in entry.models))
        return AiSettings(**selection.model_dump(), available=available)

    def put(self, owner: str, selection: AiSettingsInput) -> AiSettings:
        entry = next((entry for entry in self.catalog().providers
                      if entry.provider_path == selection.provider_path and entry.provider == selection.provider), None)
        if entry is None or not any(model.id == selection.model for model in entry.models):
            raise Problem(422, "VALIDATION_ERROR", "Unsupported provider or model.")
        if not entry.available:
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
