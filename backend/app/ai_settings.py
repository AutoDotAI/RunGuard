"""Per-user model choice and a capability catalog without credential exposure."""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .db import AiSettingsRecord, UserRepository
from .models import AiModel, AiProvider, AiProviderCatalog, AiSettings, AiSettingsInput
from .store import Problem


_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
_HOST_TTL = timedelta(seconds=20)
_API_KEY_NAMES = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "azure": ("AZURE_API_KEY",),
    "cohere": ("COHERE_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "together_ai": ("TOGETHERAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "fireworks_ai": ("FIREWORKS_AI_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _models(value: str | None, *, include_default: bool = False) -> list[AiModel]:
    ids = (["default"] if include_default else []) + (value or "").split(",")
    result: list[AiModel] = []
    seen: set[str] = set()
    for raw in ids:
        model_id = raw.strip()
        if not _MODEL_ID.fullmatch(model_id) or model_id in seen:
            continue
        seen.add(model_id)
        result.append(AiModel(id=model_id, label="CLI default model" if model_id == "default" else model_id))
    return result


def _credential_for(model_id: str, environ: dict[str, str]) -> bool:
    provider = model_id.split("/", 1)[0].lower()
    if provider.startswith("gpt-") or provider.startswith("o1") or provider.startswith("o3") or provider.startswith("o4"):
        provider = "openai"
    elif provider.startswith("claude-"):
        provider = "anthropic"
    key_names = _API_KEY_NAMES.get(provider, ())
    return any(environ.get(name, "").strip() for name in key_names)


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
            return {item for item in providers if item in {"codex", "claude", "litellm"}} if isinstance(providers, list) else set()
        except (SQLAlchemyError, ValueError, TypeError, AttributeError):
            return set()

    def catalog(self) -> AiProviderCatalog:
        environ = self.environ if self.environ is not None else os.environ
        connected = self.capability_reader()
        codex = _models(environ.get("RUNGUARD_CODEX_MODELS"), include_default=True)
        claude = _models(environ.get("RUNGUARD_CLAUDE_MODELS"), include_default=True)
        api_models = _models(environ.get("RUNGUARD_LITELLM_MODELS") or environ.get("LLM_MODEL"))
        api_has_key = bool(api_models) and all(_credential_for(item.id, environ) for item in api_models)
        providers = []
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
                         AiSettingsInput(provider_path="backend_cli", provider="codex", model="default"))
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
