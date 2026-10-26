"""Real PostgreSQL queue races and crash recovery, without network or model calls."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.main import create_app
from app.workers.runner import HandlerOutcome, WorkerAlreadyRunning, WorkerRunner


SECRET = "test-only-signing-secret-32-bytes-minimum"


@pytest.fixture
def pg_url():
    source = os.environ.get("RUNGUARD_TEST_POSTGRES_URL")
    if not source:
        pytest.skip("RUNGUARD_TEST_POSTGRES_URL is not configured")
    url = make_url(source)
    if url.get_backend_name() != "postgresql":
        pytest.fail("RUNGUARD_TEST_POSTGRES_URL must use PostgreSQL")
    schema = f"runguard_queue_{uuid4().hex}"
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


def _client(url):
    return TestClient(create_app(secret=SECRET, database_url=url))


def _register(client):
    response = client.post("/api/v1/auth/register", json={
        "email": "alice@example.com", "password": "CorrectHorse123!"})
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _worker(client, headers, number):
    response = client.post("/api/v1/workers", headers=headers, json={
        "name": f"Worker {number}", "kind": "website_monitor",
        "url": "https://example.com/path"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _enqueue(client, headers, worker_id):
    return client.post(f"/api/v1/workers/{worker_id}/runs", headers=headers)


def _race(a, b, headers, first, second):
    gate = Barrier(2)

    def submit(client, worker_id):
        gate.wait(timeout=10)
        return _enqueue(client, headers, worker_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        left = pool.submit(submit, a, first)
        right = pool.submit(submit, b, second)
        return left.result(timeout=20), right.result(timeout=20)


def test_postgres_enqueue_races_preserve_worker_and_user_limits(pg_url):
    with _client(pg_url) as a, _client(pg_url) as b:
        headers = _register(a)
        ids = [_worker(a, headers, index) for index in range(11)]
        first, second = _race(a, b, headers, ids[0], ids[0])
        assert sorted((first.status_code, second.status_code)) == [202, 409]
        assert (first if first.status_code == 409 else second).json()["error"]["code"] == "RUN_ALREADY_ACTIVE"
        for worker_id in ids[1:9]:
            assert _enqueue(a, headers, worker_id).status_code == 202
        first, second = _race(a, b, headers, ids[9], ids[10])
        assert sorted((first.status_code, second.status_code)) == [202, 429]
        assert (first if first.status_code == 429 else second).json()["error"]["code"] == "TOO_MANY_ACTIVE_RUNS"
        with a.app.state.users.sessions() as session:
            assert session.scalar(text("SELECT count(*) FROM runs")) == 10
            assert session.scalar(text("SELECT count(*) FROM run_logs")) == 10


def test_postgres_advisory_lock_claim_heartbeat_and_short_transaction(pg_url):
    with _client(pg_url) as client:
        headers = _register(client)
        worker_id = _worker(client, headers, 1)
        response = _enqueue(client, headers, worker_id)
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        observed = []

        def handle(snapshot):
            # A separate connection can lock the row while the handler runs:
            # the claim transaction was committed before external work.
            with client.app.state.users.sessions() as session:
                status = session.scalar(text(
                    "SELECT status FROM runs WHERE id = :id FOR UPDATE NOWAIT"),
                    {"id": snapshot.id})
                observed.append((status, session.scalar(text(
                    "SELECT last_seen_at FROM worker_heartbeat WHERE id = 1"))))
                session.rollback()
            return HandlerOutcome({"http_status": 200, "response_time_ms": 1,
                                   "healthy": True}, http_status=200)

        with WorkerRunner(pg_url, {"website_monitor": handle}) as runner:
            with pytest.raises(WorkerAlreadyRunning):
                with WorkerRunner(pg_url):
                    pass
            blocked = subprocess.run(
                [sys.executable, "-m", "app.workers.runner", "--once"],
                cwd=Path(__file__).resolve().parents[2],
                env={**os.environ, "RUNGUARD_DATABASE_URL": pg_url},
                capture_output=True, text=True, timeout=15, check=False)
            assert blocked.returncode == 1, blocked.stderr
            assert "Another worker owns this PostgreSQL queue" in blocked.stderr
            assert runner.run_once()
            assert not runner.run_once()
        assert observed and observed[0][0] == "running" and observed[0][1]
        final = client.get(f"/api/v1/runs/{run_id}", headers=headers).json()
        assert final["status"] == "succeeded"
        assert len([entry for entry in client.get(
            f"/api/v1/runs/{run_id}/logs", headers=headers).json()["items"]
                    if entry["event"] == "RUN_CLAIMED"]) == 1


def test_postgres_crash_reconciles_once_and_does_not_retry(pg_url):
    with _client(pg_url) as client:
        headers = _register(client)
        interrupted = _enqueue(client, headers, _worker(client, headers, 1)).json()["run_id"]
        waiting = _enqueue(client, headers, _worker(client, headers, 2)).json()["run_id"]

        def crash(_snapshot):
            raise KeyboardInterrupt("simulated process crash")

        with pytest.raises(KeyboardInterrupt):
            with WorkerRunner(pg_url, {"website_monitor": crash}) as runner:
                runner.run_once()
        with WorkerRunner(pg_url, {"website_monitor": lambda _snapshot: HandlerOutcome(
                {"http_status": 200, "response_time_ms": 1, "healthy": True},
                http_status=200)}) as runner:
            failed = client.get(f"/api/v1/runs/{interrupted}", headers=headers).json()
            assert failed["status"] == "failed"
            assert failed["error"]["code"] == "WORKER_INTERRUPTED"
            assert client.get(f"/api/v1/runs/{waiting}", headers=headers).json()["status"] == "queued"
            assert runner.run_once()
        assert client.get(f"/api/v1/runs/{waiting}", headers=headers).json()["status"] == "succeeded"
        with client.app.state.users.sessions() as session:
            assert session.scalar(text("SELECT count(*) FROM runs")) == 2
            assert session.scalar(text("SELECT count(*) FROM run_logs WHERE run_id = :id AND event = 'WORKER_INTERRUPTED'"),
                                  {"id": interrupted}) == 1
