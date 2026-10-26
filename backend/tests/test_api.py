from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import jwt
import pytest
import yaml
from fastapi.testclient import TestClient

from app.auth import ISSUER
from app.db import RunRecord
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(secret="test-only-signing-secret-32-bytes-minimum",
                               database_url=f"sqlite:///{tmp_path / 'runguard.db'}")) as test_client:
        yield test_client


def register(client, email="alice@example.com", password="CorrectHorse123!"):
    response = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return response.json()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def seeded(client, token):
    headers = bearer(token)
    workers = client.get("/api/v1/workers", headers=headers).json()["items"]
    runs = client.get("/api/v1/runs", headers=headers).json()["items"]
    return workers, runs


def test_health_returns_ready_without_auth_or_external_services(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_registration_hashes_password_and_starts_with_empty_history(client):
    identity = register(client)
    UUID(identity["user"]["id"])
    assert identity["token_type"] == "bearer"
    assert "password" not in identity
    stored = client.app.state.store.user_by_id(identity["user"]["id"])
    assert stored["password_hash"].startswith("pbkdf2_sha256$")
    assert "CorrectHorse123!" not in stored["password_hash"]
    workers, runs = seeded(client, identity["access_token"])
    assert workers == []
    assert runs == []


def test_login_token_validation_and_all_protected_paths(client):
    identity = register(client)
    assert client.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": "wrong-password"}).status_code == 401
    login = client.post("/api/v1/auth/login", json={"email": "ALICE@example.com", "password": "CorrectHorse123!"})
    assert login.status_code == 200
    worker_id = client.post("/api/v1/workers", headers=bearer(identity["access_token"]),
                            json={"name": "Private", "kind": "website_monitor", "url": "https://example.com/"}).json()["id"]
    run_id = client.post(f"/api/v1/workers/{worker_id}/runs", headers=bearer(identity["access_token"])).json()["run_id"]
    protected = [
        ("get", "/api/v1/workers"), ("post", "/api/v1/workers"),
        ("get", f"/api/v1/workers/{worker_id}"), ("patch", f"/api/v1/workers/{worker_id}"),
        ("delete", f"/api/v1/workers/{worker_id}"), ("post", f"/api/v1/workers/{worker_id}/runs"),
        ("get", "/api/v1/runs"), ("get", f"/api/v1/runs/{run_id}"),
        ("get", f"/api/v1/runs/{run_id}/logs"), ("post", f"/api/v1/runs/{run_id}/retry"),
        ("get", "/api/v1/dashboard/summary"),
    ]
    for method, path in protected:
        response = getattr(client, method)(path)
        assert response.status_code == 401, (method, path, response.text)
        assert response.json()["error"]["code"] == "UNAUTHORIZED"
    assert client.get("/health").json() == {"status": "ok"}
    assert "access_token" not in client.get("/api/v1/workers", headers=bearer(identity["access_token"])).json()
    forged = jwt.encode({"sub": identity["user"]["id"], "iss": ISSUER, "aud": "runguard-api", "exp": 4102444800}, "wrong-secret-also-long-enough-for-tests", algorithm="HS256")
    assert client.get("/api/v1/workers", headers=bearer(forged)).status_code == 401
    assert client.get("/api/v1/workers", headers=bearer("garbage")).status_code == 401
    expired = jwt.encode({"sub": identity["user"]["id"], "iss": ISSUER, "aud": "runguard-api",
                          "iat": datetime.now(timezone.utc) - timedelta(hours=9),
                          "exp": datetime.now(timezone.utc) - timedelta(hours=1)},
                         "test-only-signing-secret-32-bytes-minimum", algorithm="HS256")
    assert client.get("/api/v1/workers", headers=bearer(expired)).status_code == 401


def test_two_users_cannot_read_or_mutate_each_others_data(client):
    alice = register(client)
    bob = register(client, "bob@example.com")
    a = bearer(alice["access_token"])
    b = bearer(bob["access_token"])
    worker_id = client.post("/api/v1/workers", headers=a, json={"name": "Private", "kind": "website_monitor", "url": "https://example.com/"}).json()["id"]
    run_id = client.post(f"/api/v1/workers/{worker_id}/runs", headers=a).json()["run_id"]
    assert worker_id not in [w["id"] for w in client.get("/api/v1/workers", headers=b).json()["items"]]
    assert run_id not in [r["id"] for r in client.get("/api/v1/runs", headers=b).json()["items"]]
    assert client.get("/api/v1/dashboard/summary", headers=b).json()["recent_runs"] == []
    for method, path, body in [
        ("get", f"/api/v1/workers/{worker_id}", None),
        ("patch", f"/api/v1/workers/{worker_id}", {"name": "Stolen"}),
        ("delete", f"/api/v1/workers/{worker_id}", None),
        ("post", f"/api/v1/workers/{worker_id}/runs", None),
        ("get", f"/api/v1/runs/{run_id}", None),
        ("get", f"/api/v1/runs/{run_id}/logs", None),
        ("post", f"/api/v1/runs/{run_id}/retry", None),
    ]:
        response = getattr(client, method)(path, headers=b, json=body) if body else getattr(client, method)(path, headers=b)
        assert response.status_code == 404, (method, path, response.text)
    assert client.get("/api/v1/runs", headers=b, params={"worker_id": worker_id}).status_code == 404


def test_crud_queue_logs_retry_snapshot_and_archive(client):
    token = register(client)["access_token"]
    h = bearer(token)
    created = client.post("/api/v1/workers", headers=h, json={"name": "New check", "kind": "website_monitor", "url": "https://example.com/"})
    assert created.status_code == 201
    worker_id = created.json()["id"]
    assert client.get(f"/api/v1/workers/{worker_id}", headers=h).json()["kind"] == "website_monitor"
    queued = client.post(f"/api/v1/workers/{worker_id}/runs", headers=h)
    assert queued.status_code == 202
    run_id = queued.json()["run_id"]
    assert client.get(f"/api/v1/runs/{run_id}", headers=h).json()["status"] == "queued"
    duplicate = client.post(f"/api/v1/workers/{worker_id}/runs", headers=h)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["details"]["run_id"] == run_id
    logs = client.get(f"/api/v1/runs/{run_id}/logs", headers=h).json()
    assert [r["event"] for r in logs["items"]] == ["RUN_QUEUED"]
    assert client.get(f"/api/v1/runs/{run_id}/logs", headers=h, params={"after_id": logs["next_after_id"]}).json()["items"] == []
    assert client.patch(f"/api/v1/workers/{worker_id}", headers=h, json={"name": "Changed", "url": "https://example.org/"}).status_code == 200
    snapshot = client.get(f"/api/v1/runs/{run_id}", headers=h).json()
    assert snapshot["url"] == "https://example.com/" and snapshot["worker_name"] == "New check"
    assert client.post(f"/api/v1/runs/{run_id}/retry", headers=h).status_code == 409
    with client.app.state.users.sessions() as session:
        record = session.get(RunRecord, run_id)
        record.status = "failed"
        record.finished_at = datetime.now(timezone.utc).isoformat()
        record.duration_ms = 10
        session.commit()
    retry = client.post(f"/api/v1/runs/{run_id}/retry", headers=h)
    assert retry.status_code == 202
    retry_run = client.get(f"/api/v1/runs/{retry.json()['run_id']}", headers=h).json()
    assert retry_run["retry_of_run_id"] == run_id
    assert retry_run["url"] == snapshot["url"]
    assert client.delete(f"/api/v1/workers/{worker_id}", headers=h).status_code == 204
    assert client.get(f"/api/v1/workers/{worker_id}", headers=h).status_code == 404
    assert client.get(f"/api/v1/runs/{run_id}", headers=h).status_code == 200
    assert client.post(f"/api/v1/runs/{run_id}/retry", headers=h).json()["error"]["code"] == "WORKER_ARCHIVED"


def test_pagination_dashboard_and_validation(client):
    token = register(client)["access_token"]
    h = bearer(token)
    worker_ids = []
    for number in (1, 2):
        created = client.post("/api/v1/workers", headers=h,
                              json={"name": f"Check {number}", "kind": "website_monitor",
                                    "url": "https://example.com/"})
        worker_ids.append(created.json()["id"])
    page = client.get("/api/v1/workers", headers=h, params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 2 and len(page["items"]) == 1 and page["limit"] == 1 and page["offset"] == 1
    for status, worker_id in zip(("succeeded", "failed"), worker_ids):
        run_id = client.post(f"/api/v1/workers/{worker_id}/runs", headers=h).json()["run_id"]
        with client.app.state.users.sessions() as session:
            record = session.get(RunRecord, run_id)
            record.status = status
            record.duration_ms = 100
            record.finished_at = datetime.now(timezone.utc).isoformat()
            session.commit()
    runs = client.get("/api/v1/runs", headers=h, params={"page": 2, "page_size": 1}).json()
    assert runs["total"] == 2 and len(runs["items"]) == 1 and runs["offset"] == 1
    dashboard = client.get("/api/v1/dashboard/summary", headers=h).json()
    assert dashboard["window"] == "24h" and dashboard["total"] == 2
    assert dashboard["succeeded"] == 1 and dashboard["failed"] == 1
    assert dashboard["success_rate"] == pytest.approx(0.5)
    for url in ["http://127.0.0.1/", "https://localhost/", "file:///etc/passwd", "https://user:pass@example.com/", "https://example.com:8080/"]:
        response = client.post("/api/v1/workers", headers=h, json={"name": "Bad", "kind": "website_monitor", "url": url})
        assert response.status_code == 422, (url, response.text)
    assert client.get("/api/v1/workers", headers=h, params={"limit": 101}).status_code == 400
    assert client.get("/api/v1/dashboard/summary", headers=h, params={"window": "7d"}).status_code == 400
    assert client.post("/api/v1/auth/register", json={"email": "alice@example.com", "password": "CorrectHorse123!"}).status_code == 409


def test_contract_paths_match_backend_and_cors_is_local(client):
    contract = yaml.safe_load((Path(__file__).resolve().parents[2] / "openapi.yaml").read_text())
    methods = {"get", "post", "put", "patch", "delete"}
    expected = {(method, path) for path, item in contract["paths"].items() for method in item if method in methods}
    # AI settings routes are specified before backend implementation in issue #54.
    planned = {key for key in expected if key[1].startswith("/api/v1/ai/")}
    actual = {(method, path) for path, item in client.get("/openapi.json").json()["paths"].items()
              for method in item if method in methods}
    assert expected - planned <= actual <= expected
    allowed = client.options("/api/v1/workers", headers={"Origin": "http://localhost:5173",
                                                   "Access-Control-Request-Method": "GET"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    blocked = client.options("/api/v1/workers", headers={"Origin": "https://untrusted.example",
                                                   "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in blocked.headers
