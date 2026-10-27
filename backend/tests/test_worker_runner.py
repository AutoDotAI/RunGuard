"""Durable queue execution with temporary SQLite and injected, offline handlers."""

import json
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import RunRecord, WorkerRecord
from app.main import create_app
from app.workers.runner import HandlerOutcome, WorkerAlreadyRunning, WorkerRunner


SECRET = "test-only-signing-secret-32-bytes-minimum"


def setup_client(path):
    app = create_app(secret=SECRET, database_url=f"sqlite:///{path}")
    app.state.ai_settings.capability_reader = lambda: {"codex"}
    return TestClient(app)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def register(client):
    response = client.post("/api/v1/auth/register", json={
        "email": "alice@example.com", "password": "CorrectHorse123!"})
    assert response.status_code == 201
    return response.json()["access_token"]


def enqueue(client, token, name, kind="website_monitor"):
    worker = client.post("/api/v1/workers", headers=auth(token), json={
        "name": name, "kind": kind, "url": "https://example.com/path"})
    assert worker.status_code == 201, worker.text
    response = client.post(f"/api/v1/workers/{worker.json()['id']}/runs", headers=auth(token))
    assert response.status_code == 202, response.text
    return worker.json()["id"], response.json()["run_id"]


def run(client, token, run_id):
    response = client.get(f"/api/v1/runs/{run_id}", headers=auth(token))
    assert response.status_code == 200
    return response.json()


def logs(client, token, run_id, after_id=0):
    response = client.get(f"/api/v1/runs/{run_id}/logs?after_id={after_id}", headers=auth(token))
    assert response.status_code == 200
    return response.json()


def test_stopped_worker_queued_then_committed_claim_snapshot_and_success(tmp_path):
    path = tmp_path / "app.db"
    with setup_client(path) as client:
        token = register(client)
        worker_id, run_id = enqueue(client, token, "Original")
        assert client.get("/health").status_code == 200
        assert run(client, token, run_id)["status"] == "queued"
        with client.app.state.users.sessions() as session:
            record = session.get(WorkerRecord, worker_id)
            record.name = "Updated"
            record.url = "https://example.org/new"
            record.config_json = json.dumps({"version": 2})
            session.commit()
        observed = []

        def succeed(snapshot):
            observed.append((snapshot.id, snapshot.url, snapshot.config, run(client, token, run_id)["status"]))
            with pytest.raises(FrozenInstanceError):
                snapshot.url = "https://wrong.example/"
            snapshot.config["version"] = 99
            assert snapshot.config == {}
            assert [item["event"] for item in logs(client, token, run_id)["items"]] == [
                "RUN_QUEUED", "RUN_CLAIMED", "RUN_STARTED", "URL_VALIDATION_STARTED",
                "HTTP_REQUEST_STARTED"]
            return HandlerOutcome({"http_status": 200, "response_time_ms": 1,
                                   "healthy": True}, http_status=200)

        with WorkerRunner(f"sqlite:///{path}", {"website_monitor": succeed}) as runner:
            with pytest.raises(WorkerAlreadyRunning):
                with WorkerRunner(f"sqlite:///{path}"):
                    pass
            blocked = subprocess.run(
                [sys.executable, "-m", "app.workers.runner", "--once"],
                env={**os.environ, "RUNGUARD_DATABASE_URL": f"sqlite:///{path}"},
                capture_output=True, text=True, timeout=10, check=False)
            assert blocked.returncode == 1
            assert "Another worker owns this SQLite queue" in blocked.stderr
            assert runner.run_once() is True
            assert runner.run_once() is False
        assert observed == [(run_id, "https://example.com/path", {}, "running")]
        final = run(client, token, run_id)
        assert final["status"] == "succeeded" and final["result"] == {
            "http_status": 200, "response_time_ms": 1, "healthy": True}
        assert final["started_at"] and final["finished_at"]
        assert final["duration_ms"] >= 0
        all_logs = logs(client, token, run_id)["items"]
        assert [item["event"] for item in all_logs] == [
            "RUN_QUEUED", "RUN_CLAIMED", "RUN_STARTED", "URL_VALIDATION_STARTED",
            "HTTP_REQUEST_STARTED",
            "HTTP_RESPONSE_RECEIVED", "RUN_SUCCEEDED"]
        assert [item["id"] for item in all_logs] == sorted({item["id"] for item in all_logs})
        assert all(datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")).tzinfo
                   == timezone.utc for item in all_logs)
        assert logs(client, token, run_id, after_id=all_logs[1]["id"])["items"] == all_logs[2:]
        with WorkerRunner(f"sqlite:///{path}", {"website_monitor": succeed}) as runner:
            assert runner.run_once() is False
        assert run(client, token, run_id) == final
        assert logs(client, token, run_id)["items"] == all_logs
        with WorkerRunner(f"sqlite:///{path}") as runner:
            assert runner._finish(run_id, HandlerOutcome({"http_status": 500,
                "response_time_ms": 0, "healthy": False})) is False
        assert run(client, token, run_id) == final


def test_oldest_first_one_active_execution_and_sanitized_failure(tmp_path):
    path = tmp_path / "app.db"
    with setup_client(path) as client:
        token = register(client)
        _, first = enqueue(client, token, "First")
        _, second = enqueue(client, token, "Second")
        seen = []

        def fail(snapshot):
            seen.append(snapshot.id)
            assert run(client, token, first)["status"] == "running"
            assert run(client, token, second)["status"] == "queued"
            raise ValueError("private page content and api key")

        with WorkerRunner(f"sqlite:///{path}", {"website_monitor": fail}) as runner:
            assert runner.run_once()
            assert seen == [first]
            assert run(client, token, first)["error"] == {
                "code": "HANDLER_ERROR", "message": "Worker handler failed"}
            assert runner.run_once()
            assert seen == [first, second]
            assert not runner.run_once()
        assert all(run(client, token, item)["status"] == "failed" for item in (first, second))
        assert all("private" not in json.dumps(logs(client, token, item))
                   for item in (first, second))
        with client.app.state.users.sessions() as session:
            assert session.get(RunRecord, first).retry_of_run_id is None
            assert session.get(RunRecord, second).retry_of_run_id is None


def test_unconfigured_summarizer_rejects_enqueue_without_fabricated_result(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNGUARD_MODEL_PROVIDER", "disabled")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    path = tmp_path / "app.db"
    with setup_client(path) as client:
        client.app.state.ai_settings.capability_reader = lambda: set()
        token = register(client)
        created = client.post("/api/v1/workers", headers=auth(token), json={
            "name": "Summarizer", "kind": "url_summarizer", "url": "https://example.com/path"})
        assert created.status_code == 201
        rejected = client.post(f"/api/v1/workers/{created.json()['id']}/runs", headers=auth(token))
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"
        assert client.get("/api/v1/runs", headers=auth(token)).json()["total"] == 0


def test_cli_process_consumes_durable_queue_after_api_restart(tmp_path):
    path = tmp_path / "app.db"
    with setup_client(path) as client:
        token = register(client)
        _, run_id = enqueue(client, token, "Separate process", "url_summarizer")
        assert run(client, token, run_id)["status"] == "queued"
        with client.app.state.users.sessions() as session:
            session.get(RunRecord, run_id).url_snapshot = "http://localhost/"
            session.commit()
    completed = subprocess.run(
        [sys.executable, "-m", "app.workers.runner", "--once"],
        env={**os.environ, "RUNGUARD_DATABASE_URL": f"sqlite:///{path}",
             "RUNGUARD_MODEL_PROVIDER": "disabled", "LLM_MODEL": ""},
        capture_output=True, text=True, timeout=15, check=False)
    assert completed.returncode == 0, completed.stderr
    with setup_client(path) as client:
        final = run(client, token, run_id)
        assert final["status"] == "failed"
    assert final["error"]["code"] == "URL_INVALID"


def test_crash_reconciliation_fails_running_once_and_keeps_queued(tmp_path):
    path = tmp_path / "app.db"
    with setup_client(path) as client:
        token = register(client)
        _, interrupted = enqueue(client, token, "Interrupted")
        _, waiting = enqueue(client, token, "Waiting")

        def crash(_snapshot):
            raise KeyboardInterrupt("simulated process death")

        with pytest.raises(KeyboardInterrupt):
            with WorkerRunner(f"sqlite:///{path}", {"website_monitor": crash}) as runner:
                runner.run_once()
        assert run(client, token, interrupted)["status"] == "running"
        assert run(client, token, waiting)["status"] == "queued"
        processed = []
        with WorkerRunner(f"sqlite:///{path}", {
            "website_monitor": lambda snapshot: (
                processed.append(snapshot.id) or HandlerOutcome({
                    "http_status": 200, "response_time_ms": 1, "healthy": True}))}) as runner:
            recovered = run(client, token, interrupted)
            assert recovered["status"] == "failed"
            assert recovered["error"]["code"] == "WORKER_INTERRUPTED"
            assert recovered["finished_at"] and recovered["duration_ms"] >= 0
            assert "WORKER_INTERRUPTED" in [
                item["event"] for item in logs(client, token, interrupted)["items"]]
            assert run(client, token, waiting)["status"] == "queued"
            assert runner.run_once()
        assert processed == [waiting]
        assert run(client, token, waiting)["status"] == "succeeded"
        with WorkerRunner(f"sqlite:///{path}") as runner:
            assert runner.run_once() is False
        assert run(client, token, interrupted) == recovered
