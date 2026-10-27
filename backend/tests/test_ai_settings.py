"""Auth, isolation, and capability boundaries for AI settings."""

import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import Base
from app.main import create_app
from app.workers.runner import _worker_capabilities


SECRET = "test-only-secret-with-at-least-32-bytes"


def _register(client: TestClient, email: str) -> str:
    response = client.post("/api/v1/auth/register", json={"email": email, "password": "password123"})
    assert response.status_code == 201
    return response.json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNGUARD_ALERT_INTERVAL_SECONDS", "0")
    app = create_app(secret=SECRET, database_url=f"sqlite:///{tmp_path / 'ai.db'}")
    # The new revision follows the separate queue/alert revisions; these API
    # tests exercise the ORM table until that migration chain lands.
    app.state.users.migrate = lambda: Base.metadata.create_all(app.state.users.engine)
    return app


def test_catalog_auth_default_and_server_side_secrets(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    monkeypatch.setenv("RUNGUARD_CODEX_MODELS", "gpt-5.4, unsafe model")
    monkeypatch.setenv("RUNGUARD_LITELLM_MODELS", "openai/gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-value")
    with TestClient(app) as client:
        assert client.get("/api/v1/ai/providers").status_code == 401
        token = _register(client, "one@example.com")
        response = client.get("/api/v1/ai/providers", headers=_headers(token))
        assert response.status_code == 200
        providers = response.json()["providers"]
        assert [p["provider"] for p in providers] == ["extractive", "codex", "claude", "litellm"]
        assert all(not p["available"] for p in providers)
        assert [m["id"] for m in providers[1]["models"]] == ["default", "gpt-5.4"]
        assert "super-secret-value" not in response.text
        settings = client.get("/api/v1/ai/settings", headers=_headers(token)).json()
        assert settings == {"provider_path": "local_demo", "provider": "extractive",
                            "model": "extractive-v1", "available": False}
        assert "super-secret-value" not in json.dumps(settings)


def test_settings_validation_unavailable_and_isolation_after_restart(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        first = _register(client, "first@example.com")
        second = _register(client, "second@example.com")
        invalid = {"provider_path": "api", "provider": "codex", "model": "default"}
        response = client.put("/api/v1/ai/settings", json=invalid, headers=_headers(first))
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        wrong_model = {"provider_path": "backend_cli", "provider": "codex", "model": "unlisted-model"}
        response = client.put("/api/v1/ai/settings", json=wrong_model, headers=_headers(first))
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        valid = {"provider_path": "backend_cli", "provider": "claude", "model": "default"}
        response = client.put("/api/v1/ai/settings", json=valid, headers=_headers(first))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"
        app.state.ai_settings.capability_reader = lambda: {"codex", "claude"}
        response = client.put("/api/v1/ai/settings", json=valid, headers=_headers(first))
        assert response.status_code == 200
        assert response.json() == {**valid, "available": True}
        assert client.get("/api/v1/ai/settings", headers=_headers(second)).json()["provider"] == "extractive"

    reopened = _app(tmp_path, monkeypatch)
    reopened.state.ai_settings.capability_reader = lambda: {"claude"}
    with TestClient(reopened) as client:
        assert client.get("/api/v1/ai/settings", headers=_headers(first)).json() == {**valid, "available": True}
        assert client.get("/api/v1/ai/settings", headers=_headers(second)).json()["provider"] == "extractive"


def test_stale_heartbeat_does_not_offer_cli(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with app.state.users.engine.begin() as connection:
        connection.execute(text("CREATE TABLE worker_heartbeat (id INTEGER PRIMARY KEY, "
                                "last_seen_at VARCHAR(35) NOT NULL, capabilities_json TEXT NOT NULL)"))
        stale = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        connection.execute(text("INSERT INTO worker_heartbeat VALUES (1, :last_seen, :capabilities)"),
                           {"last_seen": stale, "capabilities": '{"providers":["codex"]}'})
    assert next(p for p in app.state.ai_settings.catalog().providers if p.provider == "codex").available is False
    with app.state.users.engine.begin() as connection:
        connection.execute(text("UPDATE worker_heartbeat SET last_seen_at = :last_seen"),
                           {"last_seen": datetime.now(timezone.utc).isoformat()})
    assert next(p for p in app.state.ai_settings.catalog().providers if p.provider == "codex").available is True


def test_litellm_requires_account_credential_even_with_server_key(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    app.state.ai_settings.capability_reader = lambda: {"litellm"}
    with TestClient(app) as client:
        token = _register(client, "api@example.com")
        choice = {"provider_path": "api", "provider": "litellm", "model": "openai/gpt-4.1-mini"}
        unavailable = client.put("/api/v1/ai/settings", json=choice, headers=_headers(token))
        assert unavailable.status_code == 409
        monkeypatch.setenv("OPENAI_API_KEY", "never-return-this-key")
        saved = client.put("/api/v1/ai/settings", json=choice, headers=_headers(token))
        assert saved.status_code == 409
        assert "never-return-this-key" not in saved.text
        assert "never-return-this-key" not in client.get("/api/v1/ai/providers", headers=_headers(token)).text


def test_litellm_catalog_offers_only_approved_models_without_account_key(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    app.state.ai_settings.capability_reader = lambda: {"litellm"}
    monkeypatch.setenv("RUNGUARD_LITELLM_MODELS", "openai/gpt-4o-mini,anthropic/claude-3-haiku")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with TestClient(app) as client:
        token = _register(client, "mixed@example.com")
        providers = client.get("/api/v1/ai/providers", headers=_headers(token)).json()["providers"]
        api = next(provider for provider in providers if provider["provider"] == "litellm")
        assert api["available"] is False
        assert [model["id"] for model in api["models"]] == [
            "openai/gpt-4.1-mini", "anthropic/claude-sonnet-4-6"]
        unavailable_model = {"provider_path": "api", "provider": "litellm",
                             "model": "anthropic/claude-3-haiku"}
        result = client.put("/api/v1/ai/settings", json=unavailable_model, headers=_headers(token))
        assert result.status_code == 422
        assert result.json()["error"]["code"] == "VALIDATION_ERROR"


def test_worker_heartbeat_alone_does_not_unlock_account_api_choice(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    monkeypatch.delenv("RUNGUARD_MODEL_PROVIDER", raising=False)
    monkeypatch.setenv("RUNGUARD_LITELLM_MODELS", "groq/llama-3.3-70b-versatile")
    monkeypatch.setenv("GROQ_API_KEY", "test-only-key")
    monkeypatch.setattr("app.workers.runner.shutil.which", lambda _name: None)
    capability = _worker_capabilities()
    assert capability == {"providers": ["extractive", "litellm"]}
    app.state.ai_settings.capability_reader = lambda: set(capability["providers"])
    api = next(provider for provider in app.state.ai_settings.catalog().providers
               if provider.provider == "litellm")
    assert api.available is False
    assert [model.id for model in api.models] == [
        "openai/gpt-4.1-mini", "anthropic/claude-sonnet-4-6"]
