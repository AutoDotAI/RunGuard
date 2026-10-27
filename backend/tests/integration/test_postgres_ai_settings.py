"""Exercise AI settings across real PostgreSQL API restarts.

RUNGUARD_TEST_POSTGRES_URL must point to a disposable PostgreSQL database.
The test creates and drops only its own schema.
"""

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.main import create_app


SECRET = "test-only-secret-with-at-least-32-bytes"


@pytest.fixture
def postgres_url():
    configured = os.environ.get("RUNGUARD_TEST_POSTGRES_URL")
    if not configured:
        pytest.skip("RUNGUARD_TEST_POSTGRES_URL is not configured")
    url = make_url(configured)
    if url.get_backend_name() != "postgresql":
        pytest.fail("RUNGUARD_TEST_POSTGRES_URL must use PostgreSQL")
    schema = f"runguard_ai_{uuid4().hex}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated = url.update_query_dict({"options": f"-csearch_path={schema}"})
    try:
        yield isolated.render_as_string(hide_password=False)
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_postgres_settings_survive_restart_with_owner_isolation(postgres_url, monkeypatch):
    monkeypatch.setenv("RUNGUARD_ALERT_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("RUNGUARD_CODEX_MODELS", "gpt-5.4")
    app = create_app(secret=SECRET, database_url=postgres_url)
    with TestClient(app) as client:
        first = client.post("/api/v1/auth/register", json={"email": "first@example.com",
                                                             "password": "password123"})
        second = client.post("/api/v1/auth/register", json={"email": "second@example.com",
                                                              "password": "password123"})
        assert first.status_code == second.status_code == 201
        first_headers = {"Authorization": f"Bearer {first.json()['access_token']}"}
        second_headers = {"Authorization": f"Bearer {second.json()['access_token']}"}
        with app.state.users.engine.begin() as connection:
            connection.execute(text("INSERT INTO worker_heartbeat "
                                    "(id, last_seen_at, capabilities_json) "
                                    "VALUES (1, :seen, '{\"providers\":[\"codex\"]}')"),
                               {"seen": datetime.now(timezone.utc).isoformat()})
        choice = {"provider_path": "backend_cli", "provider": "codex", "model": "gpt-5.4"}
        saved = client.put("/api/v1/ai/settings", json=choice, headers=first_headers)
        assert saved.status_code == 200
        assert saved.json() == {**choice, "available": True}
        assert client.get("/api/v1/ai/settings", headers=second_headers).json()["model"] == "default"

    restarted = create_app(secret=SECRET, database_url=postgres_url)
    with TestClient(restarted) as client:
        with restarted.state.users.engine.begin() as connection:
            connection.execute(text("UPDATE worker_heartbeat SET last_seen_at = :seen WHERE id = 1"),
                               {"seen": datetime.now(timezone.utc).isoformat()})
        login = client.post("/api/v1/auth/login", json={"email": "first@example.com",
                                                         "password": "password123"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert client.get("/api/v1/ai/settings", headers=headers).json() == {**choice, "available": True}
        with restarted.state.users.engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM ai_settings")) == 1


def test_selected_model_survives_edit_and_runs_through_postgres_queue(postgres_url, monkeypatch):
    import json

    from app import outbound
    from app.db import RunRecord
    from app.workers.runner import HandlerOutcome, WorkerRunner
    from app.workers.url_summarizer import summarize

    monkeypatch.setenv("RUNGUARD_ALERT_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("RUNGUARD_LITELLM_MODELS", "openai/gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-never-sent")
    monkeypatch.setenv("RUNGUARD_MODEL_PROVIDER", "disabled")
    app = create_app(secret=SECRET, database_url=postgres_url)
    seen = []
    monkeypatch.setattr("app.workers.url_summarizer.LiteLLMClient.complete",
                        lambda self, **kwargs: seen.append(kwargs["model"]) or
                        "# Article\n- Fact one.\n- Fact two.\n- Fact three.")
    body = b"<html><body><article>" + b"A readable article with facts and context. " * 5 + b"</article></body></html>"
    with TestClient(app) as client:
        app.state.ai_settings.capability_reader = lambda: {"litellm", "codex"}
        registered = client.post("/api/v1/auth/register", json={"email": "selected@example.com",
                                                                  "password": "password123"})
        assert registered.status_code == 201
        headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
        choice = {"provider_path": "api", "provider": "litellm", "model": "openai/gpt-4o-mini"}
        assert client.put("/api/v1/ai/settings", json=choice, headers=headers).status_code == 200
        created = client.post("/api/v1/workers", json={"name": "Article", "kind": "url_summarizer",
                                                        "url": "https://example.com/article"}, headers=headers)
        assert created.status_code == 201
        worker_id = created.json()["id"]
        queued = client.post(f"/api/v1/workers/{worker_id}/runs", headers=headers)
        assert queued.status_code == 202, queued.text
        run_id = queued.json()["run_id"]
        with app.state.users.sessions() as session:
            assert json.loads(session.get(RunRecord, run_id).config_snapshot_json)["model"] == choice["model"]
        assert client.put("/api/v1/ai/settings", json={"provider_path": "backend_cli",
                                                           "provider": "codex", "model": "default"},
                          headers=headers).status_code == 200
        with WorkerRunner(postgres_url, {"url_summarizer": lambda _snapshot:
                          HandlerOutcome(None, error_code="LLM_ERROR",
                                         error_message="Fake provider failure")},
                          capability_reporter=lambda: {"providers": ["litellm"]}) as runner:
            assert runner.run_once()
        assert client.get(f"/api/v1/runs/{run_id}", headers=headers).json()["status"] == "failed"
        retry = client.post(f"/api/v1/runs/{run_id}/retry", headers=headers)
        assert retry.status_code == 202
        retry_id = retry.json()["run_id"]
        with app.state.users.sessions() as session:
            original = session.get(RunRecord, run_id)
            repeated = session.get(RunRecord, retry_id)
            assert repeated.config_snapshot_json == original.config_snapshot_json
        with WorkerRunner(postgres_url, {"url_summarizer": lambda snapshot:
                          summarize(snapshot, fetch=lambda _url: outbound.FetchResponse(
                              200, {"content-type": "text/html"}, body))},
                          capability_reporter=lambda: {"providers": ["litellm"]}) as runner:
            assert runner.run_once()
        finished = client.get(f"/api/v1/runs/{retry_id}", headers=headers)
        assert finished.status_code == 200
        assert finished.json()["status"] == "succeeded"
        assert finished.json()["result"]["model"] == choice["model"]
        assert seen == [choice["model"]]
