"""Account and worker API behavior against an isolated PostgreSQL schema."""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.db import RunRecord
from app.main import create_app


SECRET = "test-only-signing-secret-32-bytes-minimum"


@pytest.fixture
def postgres_url(monkeypatch):
    configured = os.environ.get("RUNGUARD_TEST_POSTGRES_URL")
    if not configured:
        pytest.skip("RUNGUARD_TEST_POSTGRES_URL is not configured")
    url = make_url(configured)
    if url.get_backend_name() != "postgresql":
        pytest.fail("RUNGUARD_TEST_POSTGRES_URL must use PostgreSQL")
    schema = f"runguard_accounts_{uuid4().hex}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    monkeypatch.setenv("RUNGUARD_ALERT_INTERVAL_SECONDS", "0")
    try:
        yield url.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False)
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _register(client, email):
    response = client.post("/api/v1/auth/register", json={
        "email": email, "password": "CorrectHorse123!"})
    assert response.status_code == 201, response.text
    return response.json()


def _auth(identity):
    return {"Authorization": f"Bearer {identity['access_token']}"}


def test_postgres_account_restart_ownership_and_archived_history(postgres_url):
    with TestClient(create_app(secret=SECRET, database_url=postgres_url)) as client:
        alice = _register(client, "alice@example.com")
        bob = _register(client, "bob@example.com")
        stored = client.app.state.store.user_by_id(alice["user"]["id"])
        assert stored["password_hash"].startswith("pbkdf2_sha256$")
        assert "CorrectHorse123!" not in stored["password_hash"]
        created = client.post("/api/v1/workers", headers=_auth(alice), json={
            "name": "Private", "kind": "website_monitor", "url": "https://example.com/"})
        assert created.status_code == 201, created.text
        worker_id = created.json()["id"]
        run_id = str(uuid4())
        with client.app.state.users.sessions() as session:
            session.add(RunRecord(
                id=run_id, worker_id=worker_id, owner_user_id=alice["user"]["id"],
                worker_name_snapshot="Private", kind="website_monitor",
                url_snapshot="https://example.com/", config_snapshot_json="{}",
                status="failed", created_at="2026-01-01T00:00:00Z",
                finished_at="2026-01-01T00:00:01Z", duration_ms=1000,
                error_code="HTTP_ERROR", error_message="Check failed"))
            session.commit()

    with TestClient(create_app(secret=SECRET, database_url=postgres_url)) as client:
        login = client.post("/api/v1/auth/login", json={
            "email": "ALICE@example.com", "password": "CorrectHorse123!"})
        assert login.status_code == 200, login.text
        assert login.json()["user"]["id"] == alice["user"]["id"]
        assert client.post("/api/v1/auth/login", json={
            "email": "alice@example.com", "password": "wrong-password"}).status_code == 401
        assert client.get(f"/api/v1/workers/{worker_id}", headers=_auth(bob)).status_code == 404
        assert client.patch(f"/api/v1/workers/{worker_id}", headers=_auth(bob),
                            json={"name": "Stolen"}).status_code == 404
        assert client.delete(f"/api/v1/workers/{worker_id}", headers=_auth(bob)).status_code == 404
        assert client.get("/api/v1/workers", headers=_auth(bob)).json()["items"] == []
        assert client.get(f"/api/v1/workers/{worker_id}", headers=_auth(login.json())).status_code == 200
        assert client.delete(f"/api/v1/workers/{worker_id}", headers=_auth(login.json())).status_code == 204
        assert client.get(f"/api/v1/workers/{worker_id}", headers=_auth(login.json())).status_code == 404
        assert client.get(f"/api/v1/runs/{run_id}", headers=_auth(login.json())).status_code == 200
        assert client.get(f"/api/v1/runs/{run_id}", headers=_auth(bob)).status_code == 404

    with TestClient(create_app(secret=SECRET, database_url=postgres_url)) as client:
        assert client.get(f"/api/v1/runs/{run_id}", headers=_auth(alice)).status_code == 200
        assert client.get("/api/v1/workers", headers=_auth(alice)).json()["items"] == []


def test_postgres_worker_quota_is_owner_scoped(postgres_url):
    with TestClient(create_app(secret=SECRET, database_url=postgres_url)) as client:
        alice = _register(client, "alice@example.com")
        bob = _register(client, "bob@example.com")
        created_ids = []
        for number in range(20):
            response = client.post("/api/v1/workers", headers=_auth(alice), json={
                "name": f"Check {number}", "kind": "website_monitor",
                "url": "https://example.com/"})
            assert response.status_code == 201, response.text
            created_ids.append(response.json()["id"])
        blocked = client.post("/api/v1/workers", headers=_auth(alice), json={
            "name": "Check 21", "kind": "website_monitor", "url": "https://example.com/"})
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "WORKER_LIMIT_REACHED"
        assert client.post("/api/v1/workers", headers=_auth(bob), json={
            "name": "Bob's check", "kind": "website_monitor",
            "url": "https://example.com/"}).status_code == 201
        assert client.delete(f"/api/v1/workers/{created_ids[0]}", headers=_auth(alice)).status_code == 204
        assert client.post("/api/v1/workers", headers=_auth(alice), json={
            "name": "Replacement", "kind": "website_monitor",
            "url": "https://example.com/"}).status_code == 201
