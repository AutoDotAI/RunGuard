"""Authenticated self-service provider key API and catalog boundaries."""

from fastapi.testclient import TestClient

from app.ai_credentials import ensure_model_key
from app.db import Base
from app.main import create_app


SECRET = "test-only-secret-with-at-least-32-bytes"
OPENAI_MODEL = "openai/gpt-4.1-mini"
ANTHROPIC_MODEL = "anthropic/claude-sonnet-4-6"


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNGUARD_ALERT_INTERVAL_SECONDS", "0")
    model_dir = tmp_path / "model"
    model_dir.mkdir(mode=0o700)
    monkeypatch.setenv("RUNGUARD_MODEL_ENCRYPTION_KEY_FILE", str(model_dir / "model_encryption_key"))
    app = create_app(secret=SECRET, database_url=f"sqlite:///{tmp_path / 'ai.db'}")
    app.state.users.migrate = lambda: Base.metadata.create_all(app.state.users.engine)
    app.state.users.migrate()
    ensure_model_key(app.state.users, model_dir)
    app.state.ai_settings.capability_reader = lambda: {"extractive", "litellm"}
    return app


def _register(client, email):
    response = client.post("/api/v1/auth/register",
                           json={"email": email, "password": "password123"})
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _api_provider(client, headers):
    response = client.get("/api/v1/ai/providers", headers=headers)
    assert response.status_code == 200
    return next(item for item in response.json()["providers"] if item["provider"] == "litellm")


def test_key_routes_require_auth_and_validate_inputs(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        for method, path in (("get", "/api/v1/ai/credentials"),
                             ("put", "/api/v1/ai/credentials/openai"),
                             ("delete", "/api/v1/ai/credentials/openai")):
            assert getattr(client, method)(path).status_code == 401
        headers = _register(client, "one@example.com")
        missing = client.put("/api/v1/ai/credentials/openai", json={}, headers=headers)
        assert missing.status_code == 422
        short = client.put("/api/v1/ai/credentials/openai", json={"api_key": "short"},
                           headers=headers)
        assert short.status_code == 422
        spaced = client.put("/api/v1/ai/credentials/openai", json={"api_key": " sk-valid-key-123 "},
                            headers=headers)
        assert spaced.status_code == 422
        invalid = client.put("/api/v1/ai/credentials/other",
                             json={"api_key": "sk-valid-key-123"}, headers=headers)
        assert invalid.status_code == 422
        invalid_model = client.put("/api/v1/ai/settings", json={
            "provider_path": "api", "provider": "litellm", "model": "openai/unsupported"},
            headers=headers)
        assert invalid_model.status_code == 422


def test_credential_isolation_replacement_deletion_and_availability(tmp_path, monkeypatch, caplog):
    app = _app(tmp_path, monkeypatch)
    first_key = "sk-first-secret-123456"
    replacement = "sk-replaced-secret-123456"
    with TestClient(app) as client:
        a = _register(client, "a@example.com")
        b = _register(client, "b@example.com")
        assert client.get("/api/v1/ai/credentials", headers=a).json() == {
            "credentials": [{"provider": "openai", "configured": False},
                            {"provider": "anthropic", "configured": False}]}
        assert _api_provider(client, a)["available"] is False
        assert [model["id"] for model in _api_provider(client, a)["models"]] == [
            OPENAI_MODEL, ANTHROPIC_MODEL]
        response = client.put("/api/v1/ai/credentials/openai", json={"api_key": first_key},
                              headers=a)
        assert response.status_code == 200
        assert response.json() == {"provider": "openai", "configured": True}
        assert _api_provider(client, a)["available"] is True
        assert _api_provider(client, b)["available"] is False
        choice = {"provider_path": "api", "provider": "litellm", "model": OPENAI_MODEL}
        assert client.put("/api/v1/ai/settings", json=choice, headers=a).status_code == 200
        assert client.put("/api/v1/ai/settings", json=choice, headers=b).status_code == 409
        assert client.put("/api/v1/ai/settings", json={**choice, "model": ANTHROPIC_MODEL},
                          headers=a).status_code == 409
        assert client.get("/api/v1/ai/settings", headers=a).json() == {**choice, "available": True}
        assert client.delete("/api/v1/ai/credentials/openai", headers=b).json()["configured"] is False
        assert _api_provider(client, a)["available"] is True
        replace = client.put("/api/v1/ai/credentials/openai", json={"api_key": replacement},
                             headers=a)
        assert replace.status_code == 200
        owner = app.state.users.by_email("a@example.com")["id"]
        assert app.state.ai_credentials.get(owner, "openai") == replacement
        assert client.get("/api/v1/ai/credentials", headers=b).json()["credentials"][0]["configured"] is False
        deleted = client.delete("/api/v1/ai/credentials/openai", headers=a)
        assert deleted.json() == {"provider": "openai", "configured": False}
        assert client.get("/api/v1/ai/settings", headers=a).json() == {**choice, "available": False}
        assert _api_provider(client, a)["available"] is False
        output = "".join((response.text, replace.text, deleted.text,
                          client.get("/api/v1/ai/providers", headers=a).text,
                          client.get("/api/v1/ai/credentials", headers=a).text,
                          str(caplog.records)))
        assert first_key not in output and replacement not in output


def test_missing_encryption_key_fails_closed_without_echoing_secret(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    key = "sk-never-echo-this-123456"
    with TestClient(app) as client:
        headers = _register(client, "lost-key@example.com")
        assert client.put("/api/v1/ai/credentials/openai", json={"api_key": key},
                          headers=headers).status_code == 200
        (tmp_path / "model" / "model_encryption_key").unlink()
        for path in ("/api/v1/ai/credentials", "/api/v1/ai/providers", "/api/v1/ai/settings"):
            response = client.get(path, headers=headers)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "CREDENTIAL_UNAVAILABLE"
            assert key not in response.text
