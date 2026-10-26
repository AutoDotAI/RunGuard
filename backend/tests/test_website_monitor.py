"""Website Monitor contract through the API and durable worker, without network I/O."""

import json

import pytest
from fastapi.testclient import TestClient

from app import outbound
from app.db import RunRecord, WorkerRecord
from app.main import create_app
from app.workers.runner import WorkerRunner
from app.workers.website_monitor import monitor


SECRET = "test-only-signing-secret-32-bytes-minimum"


def _setup(client, url="https://example.com/check?token=private-secret"):
    token = client.post("/api/v1/auth/register", json={
        "email": "monitor@example.com", "password": "CorrectHorse123!"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    worker = client.post("/api/v1/workers", headers=headers, json={
        "name": "Website", "kind": "website_monitor", "url": url})
    assert worker.status_code == 201, worker.text
    enqueued = client.post(f"/api/v1/workers/{worker.json()['id']}/runs", headers=headers)
    assert enqueued.status_code == 202, enqueued.text
    return headers, worker.json()["id"], enqueued.json()["run_id"]


@pytest.mark.parametrize("status,expected_status,healthy", [
    (200, "succeeded", True), (302, "succeeded", True),
    (404, "failed", False), (503, "failed", False),
])
def test_http_status_and_snapshot_through_api_worker_api(tmp_path, monkeypatch, status,
                                                         expected_status, healthy):
    path = tmp_path / "monitor.db"
    calls = []

    def fake_fetch(url):
        calls.append(url)
        return outbound.FetchResponse(status, {"Location": "https://redirect.example/"}, b"private body")

    monkeypatch.setattr(outbound, "fetch", fake_fetch)

    with TestClient(create_app(secret=SECRET, database_url=f"sqlite:///{path}")) as client:
        headers, worker_id, run_id = _setup(client)
        queued = client.get(f"/api/v1/runs/{run_id}", headers=headers).json()
        assert queued["status"] == "queued" and calls == []
        with client.app.state.users.sessions() as session:
            worker = session.get(WorkerRecord, worker_id)
            worker.url = "https://different.example/new"
            session.commit()

        with WorkerRunner(f"sqlite:///{path}") as runner:
            assert runner.run_once() is True
            assert runner.run_once() is False

        assert calls == ["https://example.com/check?token=private-secret"]
        response = client.get(f"/api/v1/runs/{run_id}", headers=headers)
        assert response.status_code == 200
        run = response.json()
        assert run["status"] == expected_status
        assert run["result"]["http_status"] == status
        assert run["result"]["response_time_ms"] >= 0
        assert run["result"]["healthy"] is healthy
        assert run["duration_ms"] >= 0
        assert run["started_at"] and run["finished_at"]
        assert run["error"] == (None if healthy else {
            "code": "HTTP_STATUS_ERROR", "message": f"Received HTTP {status}"})
        with client.app.state.users.sessions() as session:
            assert session.get(RunRecord, run_id).http_status == status
        log_response = client.get(f"/api/v1/runs/{run_id}/logs", headers=headers)
        assert log_response.status_code == 200
        logs = log_response.json()["items"]
        assert [item["event"] for item in logs] == [
            "RUN_QUEUED", "RUN_CLAIMED", "RUN_STARTED", "URL_VALIDATION_STARTED",
            "HTTP_REQUEST_STARTED",
            "HTTP_RESPONSE_RECEIVED", "RUN_SUCCEEDED" if healthy else "HTTP_STATUS_ERROR",
            *([] if healthy else ["RUN_FAILED"])]
        assert [item["id"] for item in logs] == sorted(item["id"] for item in logs)
        assert all("private-secret" not in item["message"] and "private body" not in item["message"]
                   for item in logs)
        if not healthy:
            assert "private-secret" not in json.dumps(run["error"])


@pytest.mark.parametrize("code,message", [
    ("FETCH_TIMEOUT", "Destination request timed out"),
    ("DNS_ERROR", "Destination resolution failed"),
    ("FETCH_ERROR", "Destination request failed"),
    ("DESTINATION_BLOCKED", "Destination address is not public"),
    ("URL_INVALID", "A valid public HTTP(S) URL is required"),
    ("RESPONSE_TOO_LARGE", "Response exceeds size limit"),
])
def test_outbound_failures_remain_sanitized_and_have_no_result(tmp_path, code, message):
    path = tmp_path / "failure.db"
    calls = []

    def fake_fetch(url):
        calls.append(url)
        raise outbound.OutboundError(code, message)

    with TestClient(create_app(secret=SECRET, database_url=f"sqlite:///{path}")) as client:
        headers, _, run_id = _setup(client)
        with WorkerRunner(f"sqlite:///{path}", {
            "website_monitor": lambda snapshot: monitor(snapshot, fetch=fake_fetch)}) as runner:
            assert runner.run_once()
            assert not runner.run_once()
        assert len(calls) == 1
        run = client.get(f"/api/v1/runs/{run_id}", headers=headers).json()
        assert run["status"] == "failed"
        assert run["result"] is None
        assert run["error"] == {"code": code, "message": message}
        assert run["duration_ms"] >= 0
        with client.app.state.users.sessions() as session:
            assert session.get(RunRecord, run_id).http_status is None
        logs = client.get(f"/api/v1/runs/{run_id}/logs", headers=headers).json()["items"]
        assert [item["event"] for item in logs] == [
            "RUN_QUEUED", "RUN_CLAIMED", "RUN_STARTED", "URL_VALIDATION_STARTED",
            "HTTP_REQUEST_STARTED",
            "HTTP_REQUEST_FAILED", code, "RUN_FAILED"]
        assert "private-secret" not in json.dumps(logs)
