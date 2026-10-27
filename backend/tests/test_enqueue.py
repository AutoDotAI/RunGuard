"""Durable, atomic enqueue behavior without a job worker or network calls."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier
from uuid import UUID

from fastapi.testclient import TestClient

from app.db import RunRecord, WorkerRecord
from app.main import create_app


SECRET = "test-only-signing-secret-32-bytes-minimum"


def app_for(path: Path):
    app = create_app(secret=SECRET, database_url=f"sqlite:///{path}")
    app.state.ai_settings.capability_reader = lambda: {"codex"}
    return app


def auth(token: str):
    return {"Authorization": f"Bearer {token}"}


def register(client, email: str):
    response = client.post("/api/v1/auth/register", json={
        "email": email, "password": "CorrectHorse123!"})
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def worker(client, token: str, name: str, kind: str = "website_monitor"):
    response = client.post("/api/v1/workers", headers=auth(token), json={
        "name": name, "kind": kind, "url": "https://example.com/path"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def counts(path: Path):
    with sqlite3.connect(path) as connection:
        return (connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM run_logs").fetchone()[0])


def concurrent_posts(client_a, client_b, token, worker_a, worker_b):
    barrier = Barrier(2)

    def submit(client, worker_id):
        barrier.wait(timeout=10)
        return client.post(f"/api/v1/workers/{worker_id}/runs", headers=auth(token))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit, client_a, worker_a)
        second = pool.submit(submit, client_b, worker_b)
        return first.result(timeout=20), second.result(timeout=20)


def test_enqueue_commits_snapshot_log_and_survives_restart_without_executor(tmp_path):
    path = tmp_path / "app.db"
    with TestClient(app_for(path)) as client:
        token = register(client, "alice@example.com")
        worker_id = worker(client, token, "Original", "url_summarizer")
        with client.app.state.users.sessions() as session:
            record = session.get(WorkerRecord, worker_id)
            record.config_json = json.dumps({"model": "test-model", "version": 1})
            session.commit()
        queued = client.post(f"/api/v1/workers/{worker_id}/runs", headers=auth(token))
        assert queued.status_code == 202, queued.text
        payload = queued.json()
        assert payload["worker_id"] == worker_id
        assert payload["status"] == "queued"
        UUID(payload["run_id"])
        assert datetime.fromisoformat(payload["created_at"].replace("Z", "+00:00")).tzinfo == timezone.utc
        run_id = payload["run_id"]
        assert counts(path) == (1, 1)
        assert client.get(f"/api/v1/runs/{run_id}", headers=auth(token)).json()["status"] == "queued"
        logs = client.get(f"/api/v1/runs/{run_id}/logs", headers=auth(token)).json()["items"]
        assert len(logs) == 1 and logs[0]["event"] == "RUN_QUEUED"
        assert logs[0]["timestamp"] == payload["created_at"]
        assert client.patch(f"/api/v1/workers/{worker_id}", headers=auth(token), json={
            "name": "Changed", "url": "https://example.org/"}).status_code == 200
        with client.app.state.users.sessions() as session:
            record = session.get(WorkerRecord, worker_id)
            record.config_json = json.dumps({"model": "other-model", "version": 2})
            session.commit()

    with TestClient(app_for(path)) as restarted:
        run = restarted.get(f"/api/v1/runs/{run_id}", headers=auth(token)).json()
        assert (run["worker_name"], run["kind"], run["url"], run["status"]) == (
            "Original", "url_summarizer", "https://example.com/path", "queued")
        with restarted.app.state.users.sessions() as session:
            record = session.get(RunRecord, run_id)
            assert json.loads(record.config_snapshot_json) == {
                "provider_path": "backend_cli", "provider": "codex", "model": "default",
                "version": 1}
        assert restarted.get(f"/api/v1/runs/{run_id}/logs", headers=auth(token)).json()["items"] == logs
        assert counts(path) == (1, 1)


def test_same_worker_concurrent_enqueue_across_app_instances(tmp_path):
    path = tmp_path / "app.db"
    with TestClient(app_for(path)) as a, TestClient(app_for(path)) as b:
        token = register(a, "alice@example.com")
        worker_id = worker(a, token, "One")
        first, second = concurrent_posts(a, b, token, worker_id, worker_id)
        by_status = {response.status_code: response for response in (first, second)}
        assert set(by_status) == {202, 409}, (first.text, second.text)
        run_id = by_status[202].json()["run_id"]
        assert by_status[409].json()["error"]["code"] == "RUN_ALREADY_ACTIVE"
        assert by_status[409].json()["error"]["details"] == {"run_id": run_id}
        assert counts(path) == (1, 1)
        with sqlite3.connect(path) as connection:
            indexes = connection.execute("PRAGMA index_list(runs)").fetchall()
            assert any(row[1] == "uq_runs_one_active_per_worker" and row[2] == 1 and row[4] == 1
                       for row in indexes)


def test_user_quota_is_atomic_across_app_instances_and_terminal_run_frees_slot(tmp_path):
    path = tmp_path / "app.db"
    with TestClient(app_for(path)) as a, TestClient(app_for(path)) as b:
        alice = register(a, "alice@example.com")
        bob = register(a, "bob@example.com")
        ids = [worker(a, alice, f"Worker {number}") for number in range(11)]
        first_run = None
        for worker_id in ids[:9]:
            response = a.post(f"/api/v1/workers/{worker_id}/runs", headers=auth(alice))
            assert response.status_code == 202, response.text
            first_run = first_run or response.json()["run_id"]
        first, second = concurrent_posts(a, b, alice, ids[9], ids[10])
        assert sorted((first.status_code, second.status_code)) == [202, 429]
        rejected = first if first.status_code == 429 else second
        assert rejected.json()["error"]["code"] == "TOO_MANY_ACTIVE_RUNS"
        assert counts(path) == (10, 10)
        bob_id = worker(a, bob, "Bob's worker")
        assert b.post(f"/api/v1/workers/{bob_id}/runs", headers=auth(bob)).status_code == 202
        with a.app.state.users.sessions() as session:
            run = session.get(RunRecord, first_run)
            run.status = "failed"
            run.finished_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            session.commit()
        retry_worker = ids[9] if first.status_code == 429 else ids[10]
        assert a.post(f"/api/v1/workers/{retry_worker}/runs", headers=auth(alice)).status_code == 202
        assert counts(path) == (12, 12)


def test_failed_enqueue_does_not_mutate_runs_or_logs(tmp_path):
    path = tmp_path / "app.db"
    with TestClient(app_for(path)) as client:
        alice = register(client, "alice@example.com")
        bob = register(client, "bob@example.com")
        alice_id = worker(client, alice, "Alice")
        archived_id = worker(client, alice, "Archived")
        assert client.delete(f"/api/v1/workers/{archived_id}", headers=auth(alice)).status_code == 204
        for worker_id, headers, expected in [
            (alice_id, {}, 401),
            (alice_id, auth("bad-token"), 401),
            (alice_id, auth(bob), 404),
            (str(UUID(int=0)), auth(alice), 404),
            (archived_id, auth(alice), 404),
            ("not-a-uuid", auth(alice), 400),
        ]:
            response = client.post(f"/api/v1/workers/{worker_id}/runs", headers=headers)
            assert response.status_code == expected, response.text
            assert counts(path) == (0, 0)
        queued = client.post(f"/api/v1/workers/{alice_id}/runs", headers=auth(alice))
        assert queued.status_code == 202
        duplicate = client.post(f"/api/v1/workers/{alice_id}/runs", headers=auth(alice))
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["details"] == {"run_id": queued.json()["run_id"]}
        assert counts(path) == (1, 1)
