"""Worker CRUD acceptance checks against a temporary SQLite database."""

from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.db import RunRecord
from app.main import create_app


SECRET = "test-only-signing-secret-32-bytes-minimum"


def app_for(database):
    return create_app(secret=SECRET, database_url=f"sqlite:///{database}")


def register(client, email="alice@example.com"):
    response = client.post("/api/v1/auth/register", json={
        "email": email, "password": "CorrectHorse123!"})
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create(client, headers, name="A check", kind="website_monitor", url="https://example.com/"):
    return client.post("/api/v1/workers", headers=headers, json={
        "name": name, "kind": kind, "url": url})


def test_empty_crud_and_stable_owned_pagination(tmp_path):
    with TestClient(app_for(tmp_path / "workers.db")) as client:
        alice = register(client)
        empty = client.get("/api/v1/workers", headers=alice).json()
        assert empty == {"items": [], "total": 0, "limit": 20, "offset": 0}

        created = create(client, alice, "  " + "A" * 80 + "  ")
        assert created.status_code == 201, created.text
        worker = created.json()
        UUID(worker["id"])
        assert worker["name"] == "A" * 80
        assert worker["last_run"] is None
        for field in ("created_at", "updated_at"):
            assert datetime.fromisoformat(worker[field].replace("Z", "+00:00")).utcoffset().total_seconds() == 0
        assert client.get(f"/api/v1/workers/{worker['id']}", headers=alice).json() == worker

        for number in range(4):
            response = create(client, alice, f"Summary {number}", "url_summarizer")
            assert response.status_code == 201, response.text
        first = client.get("/api/v1/workers", headers=alice, params={"limit": 2}).json()
        second = client.get("/api/v1/workers", headers=alice, params={"limit": 2, "offset": 2}).json()
        third = client.get("/api/v1/workers", headers=alice, params={"limit": 2, "offset": 4}).json()
        assert [page["total"] for page in (first, second, third)] == [5, 5, 5]
        all_ids = [item["id"] for page in (first, second, third) for item in page["items"]]
        assert len(all_ids) == len(set(all_ids)) == 5
        items = [item for page in (first, second, third) for item in page["items"]]
        assert [(item["created_at"], item["id"]) for item in items] == sorted(
            ((item["created_at"], item["id"]) for item in items), reverse=True)
        assert client.get("/api/v1/workers", headers=alice, params={"offset": 99}).json()["items"] == []
        for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"limit": "bad"}):
            response = client.get("/api/v1/workers", headers=alice, params=params)
            assert response.status_code == 400, (params, response.text)
            assert response.json()["error"]["code"] == "INVALID_QUERY"


@pytest.mark.parametrize("payload", [
    {},
    {"name": "", "kind": "website_monitor", "url": "https://example.com/"},
    {"name": "   ", "kind": "website_monitor", "url": "https://example.com/"},
    {"name": "A" * 81, "kind": "website_monitor", "url": "https://example.com/"},
    {"name": "Name", "kind": "unknown", "url": "https://example.com/"},
    {"name": "Name", "kind": "website_monitor", "url": "ftp://example.com/"},
    {"name": "Name", "kind": "website_monitor", "url": "https://user:pass@example.com/"},
    {"name": "Name", "kind": "website_monitor", "url": "http://127.1/"},
    {"name": "Name", "kind": "website_monitor", "url": "http://127.0.0.1/"},
    {"name": "Name", "kind": "website_monitor", "url": "https://localhost/"},
    {"name": "Name", "kind": "website_monitor", "url": "https://example.com/" + "x" * 2048},
    {"name": "Name", "kind": "website_monitor", "url": "https://example.com/", "owner_user_id": "forged"},
])
def test_create_rejects_invalid_fields_and_unsafe_save_time_urls(tmp_path, payload):
    with TestClient(app_for(tmp_path / "validation.db")) as client:
        alice = register(client)
        response = client.post("/api/v1/workers", headers=alice, json=payload)
        assert response.status_code == 422, (payload, response.text)
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert "request_id" in response.json()
        assert client.get("/api/v1/workers", headers=alice).json()["total"] == 0


def test_update_rejects_kind_change_and_archive_frees_quota_slot(tmp_path):
    with TestClient(app_for(tmp_path / "quota.db")) as client:
        alice = register(client)
        for number in range(20):
            assert create(client, alice, f"Check {number}").status_code == 201
        page = client.get("/api/v1/workers", headers=alice).json()
        assert page["total"] == 20
        worker_id = page["items"][0]["id"]
        full = create(client, alice, "Extra")
        assert full.status_code == 429
        assert full.json()["error"]["code"] == "WORKER_LIMIT_REACHED"
        for payload in ({"kind": "url_summarizer"}, {"name": ""}, {"name": "A" * 81},
                        {"url": "https://localhost/"}, {"name": "Updated", "unexpected": 1}):
            response = client.patch(f"/api/v1/workers/{worker_id}", headers=alice, json=payload)
            assert response.status_code == 422, (payload, response.text)
        updated = client.patch(f"/api/v1/workers/{worker_id}", headers=alice,
                               json={"name": "  Updated  ", "url": "https://example.org/"})
        assert updated.status_code == 200
        assert updated.json()["name"] == "Updated"
        assert updated.json()["kind"] == "website_monitor"
        deleted = client.delete(f"/api/v1/workers/{worker_id}", headers=alice)
        assert deleted.status_code == 204 and deleted.content == b""
        assert create(client, alice, "Replacement").status_code == 201
        assert client.get("/api/v1/workers", headers=alice).json()["total"] == 20


def test_edit_archive_and_queued_history_survive_restart(tmp_path):
    database = tmp_path / "restart.db"
    with TestClient(app_for(database)) as client:
        alice = register(client)
        worker = create(client, alice, "Original").json()
        worker_id = worker["id"]
        queued = client.post(f"/api/v1/workers/{worker_id}/runs", headers=alice)
        assert queued.status_code == 202
        run_id = queued.json()["run_id"]
        edited = client.patch(f"/api/v1/workers/{worker_id}", headers=alice, json={
            "name": "Changed", "url": "https://example.org/"}).json()
        assert edited["updated_at"] >= worker["updated_at"]
    with TestClient(app_for(database)) as client:
        saved = client.get(f"/api/v1/workers/{worker_id}", headers=alice).json()
        assert saved["name"] == "Changed" and saved["url"] == "https://example.org/"
        assert saved["last_run"]["id"] == run_id
        original = client.get(f"/api/v1/runs/{run_id}", headers=alice).json()
        assert original["worker_name"] == "Original" and original["url"] == "https://example.com/"
        with client.app.state.users.sessions() as session:
            assert session.get(RunRecord, run_id).config_snapshot_json == "{}"
        assert client.delete(f"/api/v1/workers/{worker_id}", headers=alice).status_code == 204
        assert client.get(f"/api/v1/workers/{worker_id}", headers=alice).status_code == 404
        assert client.delete(f"/api/v1/workers/{worker_id}", headers=alice).status_code == 404
        assert client.patch(f"/api/v1/workers/{worker_id}", headers=alice,
                            json={"name": "Nope"}).status_code == 404
        assert client.post(f"/api/v1/workers/{worker_id}/runs", headers=alice).status_code == 404
    with TestClient(app_for(database)) as client:
        assert client.get("/api/v1/workers", headers=alice).json()["items"] == []
        assert client.get(f"/api/v1/runs/{run_id}", headers=alice).json() == original
        logs = client.get(f"/api/v1/runs/{run_id}/logs", headers=alice).json()["items"]
        assert [entry["event"] for entry in logs] == ["RUN_QUEUED"]
        assert client.get(f"/api/v1/runs/{run_id}", headers=alice).json()["status"] == "queued"


def test_worker_crud_requires_token_and_hides_foreign_ids(tmp_path):
    with TestClient(app_for(tmp_path / "owners.db")) as client:
        alice = register(client)
        bob = register(client, "bob@example.com")
        worker_id = create(client, alice).json()["id"]
        for method, path, body in (
            ("get", "/api/v1/workers", None),
            ("post", "/api/v1/workers", {"name": "Bad", "kind": "website_monitor", "url": "https://example.com/"}),
            ("get", f"/api/v1/workers/{worker_id}", None),
            ("patch", f"/api/v1/workers/{worker_id}", {"name": "Bad"}),
            ("delete", f"/api/v1/workers/{worker_id}", None),
        ):
            response = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
            assert response.status_code == 401, (method, path, response.text)
        assert client.get("/api/v1/workers", headers=bob).json()["items"] == []
        assert client.get("/api/v1/workers", headers={"Authorization": "Bearer invalid"}).status_code == 401
        for method, body in (("get", None), ("patch", {"name": "Stolen"}), ("delete", None), ("post", None)):
            path = f"/api/v1/workers/{worker_id}" + ("/runs" if method == "post" else "")
            response = getattr(client, method)(path, headers=bob, json=body) if body is not None else getattr(client, method)(path, headers=bob)
            assert response.status_code == 404, (method, path, response.text)
        assert client.get(f"/api/v1/workers/{worker_id}", headers=alice).status_code == 200
