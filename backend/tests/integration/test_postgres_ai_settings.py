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
