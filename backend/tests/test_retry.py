"""Manual retry uses immutable run snapshots and the durable SQLite queue."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.db import RunLogRecord, RunRecord, WorkerRecord
from app.main import create_app
from app.workers.runner import HandlerOutcome, WorkerRunner


SECRET = "test-only-signing-secret-32-bytes-minimum"


def app_for(path: Path):
    return create_app(secret=SECRET, database_url=f"sqlite:///{path}")


def auth(token: str):
    return {"Authorization": f"Bearer {token}"}


def register(client, email: str):
    response = client.post("/api/v1/auth/register", json={
        "email": email, "password": "CorrectHorse123!"})
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def create_worker(client, token: str, name: str, kind="website_monitor"):
    response = client.post("/api/v1/workers", headers=auth(token), json={
        "name": name, "kind": kind, "url": "https://example.com/original"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def enqueue(client, token: str, worker_id: str):
    response = client.post(f"/api/v1/workers/{worker_id}/runs", headers=auth(token))
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def fail_run(client, run_id: str):
    with client.app.state.users.sessions() as session:
        run = session.get(RunRecord, run_id)
        run.status = "failed"
        run.started_at = "2026-01-01T00:00:00Z"
        run.finished_at = "2026-01-01T00:00:01Z"
        run.duration_ms = 1000
        result = ({"http_status": 503, "response_time_ms": 1000, "healthy": False}
                  if run.kind == "website_monitor" else
                  {"source_url": run.url_snapshot, "page_title": None,
                   "summary": "Original partial summary", "model": "offline-test",
                   "input_was_truncated": False})
        run.result_json = json.dumps(result)
        run.http_status = 503
        run.error_code = "TEST_FAILURE"
        run.error_message = "Offline test failure"
        session.commit()


def counts(path: Path):
    with sqlite3.connect(path) as connection:
        return (connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM run_logs").fetchone()[0])


def concurrent_requests(a, b, token, first_path, second_path):
    barrier = Barrier(2)

    def submit(client, path):
        barrier.wait(timeout=10)
        return client.post(path, headers=auth(token))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit, a, first_path)
        second = pool.submit(submit, b, second_path)
        return first.result(timeout=20), second.result(timeout=20)


@pytest.mark.parametrize("kind", ["website_monitor", "url_summarizer"])
def test_retry_preserves_original_snapshot_and_logs_across_restart(tmp_path, kind):
    path = tmp_path / "app.db"
    config = json.dumps({"original": kind, "version": 1}, separators=(",", ":"))
    observed = []
    with TestClient(app_for(path)) as client:
        token = register(client, "alice@example.com")
        worker_id = create_worker(client, token, "Original", kind)
        with client.app.state.users.sessions() as session:
            session.get(WorkerRecord, worker_id).config_json = config
            session.commit()
        source_id = enqueue(client, token, worker_id)
        fail_run(client, source_id)
        with client.app.state.users.sessions() as session:
            worker = session.get(WorkerRecord, worker_id)
            worker.name = "Edited"
            worker.url = "https://example.org/edited"
            worker.config_json = '{"version":2}'
            # The API keeps kind immutable, but the source row must remain
            # authoritative even if the current worker row later differs.
            worker.kind = "url_summarizer" if kind == "website_monitor" else "website_monitor"
            source = session.get(RunRecord, source_id)
            session.add(RunLogRecord(run_id=source_id, owner_user_id=source.owner_user_id,
                                     timestamp="2026-01-01T00:00:01Z", level="ERROR",
                                     event="TEST_FAILURE", message="Offline test failure"))
            session.commit()
        source_before = client.get(f"/api/v1/runs/{source_id}", headers=auth(token)).json()
        logs_before = client.get(f"/api/v1/runs/{source_id}/logs", headers=auth(token)).json()

        retry = client.post(f"/api/v1/runs/{source_id}/retry", headers=auth(token))
        assert retry.status_code == 202, retry.text
        payload = retry.json()
        new_id = payload["run_id"]
        assert UUID(new_id) != UUID(source_id)
        assert payload["worker_id"] == worker_id and payload["status"] == "queued"
        assert datetime.fromisoformat(payload["created_at"].replace("Z", "+00:00")).tzinfo == timezone.utc
        assert client.get(f"/api/v1/runs/{source_id}", headers=auth(token)).json() == source_before
        assert client.get(f"/api/v1/runs/{source_id}/logs", headers=auth(token)).json() == logs_before
        assert client.get("/api/v1/runs", headers=auth(token)).json()["total"] == 2
        new_run = client.get(f"/api/v1/runs/{new_id}", headers=auth(token)).json()
        assert (new_run["worker_name"], new_run["kind"], new_run["url"], new_run["status"],
                new_run["retry_of_run_id"]) == (
                    "Original", kind, "https://example.com/original", "queued", source_id)
        with client.app.state.users.sessions() as session:
            original = session.get(RunRecord, source_id)
            retried = session.get(RunRecord, new_id)
            assert retried.config_snapshot_json == original.config_snapshot_json == config
            assert retried.kind == original.kind == kind
            assert json.loads(original.result_json) == source_before["result"]
            assert original.http_status == 503
            assert retried.result_json is None and retried.error_code is None
        queued_logs = client.get(f"/api/v1/runs/{new_id}/logs", headers=auth(token)).json()["items"]
        assert len(queued_logs) == 1 and queued_logs[0]["event"] == "RUN_QUEUED"
        assert queued_logs[0]["timestamp"] == payload["created_at"]

    with TestClient(app_for(path)) as restarted:
        assert restarted.get(f"/api/v1/runs/{source_id}", headers=auth(token)).json() == source_before
        assert restarted.get(f"/api/v1/runs/{source_id}/logs", headers=auth(token)).json() == logs_before
        assert restarted.get(f"/api/v1/runs/{new_id}/logs", headers=auth(token)).json()["items"] == queued_logs
        assert restarted.get(f"/api/v1/runs/{new_id}", headers=auth(token)).json() == new_run

        def handle(snapshot):
            observed.append((snapshot.id, snapshot.kind, snapshot.url, snapshot.config_json))
            return HandlerOutcome(None, error_code="TEST_FAILURE", error_message="Offline test failure")

        with WorkerRunner(f"sqlite:///{path}", {kind: handle}) as runner:
            assert runner.run_once() is True
            assert runner.run_once() is False
        assert observed == [(new_id, kind, "https://example.com/original", config)]
        assert restarted.get(f"/api/v1/runs/{new_id}", headers=auth(token)).json()["status"] == "failed"
        assert restarted.get(f"/api/v1/runs/{source_id}", headers=auth(token)).json() == source_before
        with WorkerRunner(f"sqlite:///{path}", {kind: handle}) as runner:
            assert runner.run_once() is False
        assert len(observed) == 1
        assert counts(path) == (2, len(logs_before["items"]) +
                                len(restarted.get(f"/api/v1/runs/{new_id}/logs", headers=auth(token)).json()["items"]))


def test_retry_rejections_are_private_and_leave_no_records(tmp_path):
    path = tmp_path / "app.db"
    with TestClient(app_for(path)) as client:
        alice = register(client, "alice@example.com")
        bob = register(client, "bob@example.com")
        worker_id = create_worker(client, alice, "Alice")
        source_id = enqueue(client, alice, worker_id)
        url = f"/api/v1/runs/{source_id}/retry"
        for headers, status, code in [
            ({}, 401, "UNAUTHORIZED"), (auth("invalid"), 401, "UNAUTHORIZED"),
            (auth(bob), 404, "NOT_FOUND"),
        ]:
            response = client.post(url, headers=headers)
            assert (response.status_code, response.json()["error"]["code"]) == (status, code)
            assert counts(path) == (1, 1)
        missing = client.post(f"/api/v1/runs/{UUID(int=0)}/retry", headers=auth(alice))
        assert (missing.status_code, missing.json()["error"]["code"]) == (404, "NOT_FOUND")
        for state in ("queued", "running", "succeeded"):
            with client.app.state.users.sessions() as session:
                session.get(RunRecord, source_id).status = state
                session.commit()
            response = client.post(url, headers=auth(alice))
            assert (response.status_code, response.json()["error"]["code"]) == (409, "INVALID_RETRY_STATE")
            assert counts(path) == (1, 1)
        fail_run(client, source_id)
        assert client.delete(f"/api/v1/workers/{worker_id}", headers=auth(alice)).status_code == 204
        archived = client.post(url, headers=auth(alice))
        assert (archived.status_code, archived.json()["error"]["code"]) == (409, "WORKER_ARCHIVED")
        assert counts(path) == (1, 1)
        assert client.get(f"/api/v1/runs/{source_id}", headers=auth(alice)).status_code == 200


def test_concurrent_retries_and_run_now_share_one_worker_slot(tmp_path):
    path = tmp_path / "app.db"
    with TestClient(app_for(path)) as a, TestClient(app_for(path)) as b:
        token = register(a, "alice@example.com")
        worker_id = create_worker(a, token, "One")
        source_id = enqueue(a, token, worker_id)
        fail_run(a, source_id)
        retry_path = f"/api/v1/runs/{source_id}/retry"
        first, second = concurrent_requests(a, b, token, retry_path, retry_path)
        assert sorted((first.status_code, second.status_code)) == [202, 409]
        accepted = first if first.status_code == 202 else second
        rejected = first if first.status_code == 409 else second
        assert rejected.json()["error"] == {
            "code": "RUN_ALREADY_ACTIVE", "message": "This worker already has an active run",
            "details": {"run_id": accepted.json()["run_id"]}}
        assert counts(path) == (2, 2)

        with a.app.state.users.sessions() as session:
            session.get(RunRecord, accepted.json()["run_id"]).status = "failed"
            session.commit()
        run_now = f"/api/v1/workers/{worker_id}/runs"
        first, second = concurrent_requests(a, b, token, retry_path, run_now)
        assert sorted((first.status_code, second.status_code)) == [202, 409]
        accepted = first if first.status_code == 202 else second
        rejected = first if first.status_code == 409 else second
        assert rejected.json()["error"]["code"] == "RUN_ALREADY_ACTIVE"
        assert rejected.json()["error"]["details"] == {"run_id": accepted.json()["run_id"]}
        assert counts(path) == (3, 3)


def test_concurrent_retry_user_quota_and_two_user_isolation(tmp_path):
    path = tmp_path / "app.db"
    with TestClient(app_for(path)) as a, TestClient(app_for(path)) as b:
        alice = register(a, "alice@example.com")
        bob = register(a, "bob@example.com")
        source_ids = []
        for number in range(2):
            worker_id = create_worker(a, alice, f"Retry {number}")
            run_id = enqueue(a, alice, worker_id)
            fail_run(a, run_id)
            source_ids.append(run_id)
        for number in range(9):
            worker_id = create_worker(a, alice, f"Active {number}")
            enqueue(a, alice, worker_id)
        assert counts(path) == (11, 11)
        first, second = concurrent_requests(a, b, alice, *(
            f"/api/v1/runs/{run_id}/retry" for run_id in source_ids))
        assert sorted((first.status_code, second.status_code)) == [202, 429]
        rejected = first if first.status_code == 429 else second
        assert rejected.json()["error"]["code"] == "TOO_MANY_ACTIVE_RUNS"
        assert counts(path) == (12, 12)
        bob_worker = create_worker(b, bob, "Bob")
        assert b.post(f"/api/v1/workers/{bob_worker}/runs", headers=auth(bob)).status_code == 202
        assert counts(path) == (13, 13)
        for run_id in source_ids:
            assert b.post(f"/api/v1/runs/{run_id}/retry", headers=auth(bob)).status_code == 404
        assert counts(path) == (13, 13)
