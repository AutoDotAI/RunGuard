"""FR-07 log visibility, cursor, isolation, and redaction through the real API."""

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import outbound
from app.db import RunLogRecord
from app.main import create_app
from app.run_logs import MAX_MESSAGE_LENGTH, new_run_log
from app.workers.llm import LiteLLMClient
from app.workers.runner import HandlerOutcome, WorkerRunner


SECRET = "test-only-signing-secret-32-bytes-minimum"
SENTINEL = "PRIVATE_PAGE_TOKEN_AND_PROMPT_123"


def client_for(path):
    return TestClient(create_app(secret=SECRET, database_url=f"sqlite:///{path}"))


def register(client, email):
    token = client.post("/api/v1/auth/register", json={
        "email": email, "password": "CorrectHorse123!"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def enqueue(client, headers, kind="website_monitor"):
    worker = client.post("/api/v1/workers", headers=headers, json={
        "name": "Evidence", "kind": kind,
        "url": f"https://example.com/article?key={SENTINEL}"})
    assert worker.status_code == 201, worker.text
    run = client.post(f"/api/v1/workers/{worker.json()['id']}/runs", headers=headers)
    assert run.status_code == 202, run.text
    return worker.json()["id"], run.json()["run_id"]


def poll(client, headers, run_id, cursor=0):
    response = client.get(f"/api/v1/runs/{run_id}/logs?after_id={cursor}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_cursor_live_rows_restart_archive_and_owner_isolation(tmp_path):
    path = tmp_path / "logs.db"
    with client_for(path) as client:
        alice = register(client, "alice@example.com")
        bob = register(client, "bob@example.com")
        worker_id, run_id = enqueue(client, alice)
        queued = poll(client, alice, run_id)
        assert [row["event"] for row in queued["items"]] == ["RUN_QUEUED"]
        cursor = queued["next_after_id"]
        assert poll(client, alice, run_id, cursor) == {"items": [], "next_after_id": cursor}

        def handler(_snapshot):
            active = poll(client, alice, run_id, cursor)
            assert [row["event"] for row in active["items"]] == [
                "RUN_CLAIMED", "RUN_STARTED", "URL_VALIDATION_STARTED",
                "HTTP_REQUEST_STARTED"]
            assert client.get(f"/api/v1/runs/{run_id}", headers=alice).json()["status"] == "running"
            return HandlerOutcome({"http_status": 200, "response_time_ms": 1, "healthy": True},
                                  http_status=200)

        with WorkerRunner(f"sqlite:///{path}", {"website_monitor": handler}) as runner:
            assert runner.run_once()
        latest = poll(client, alice, run_id, cursor)
        assert [row["event"] for row in latest["items"]][-2:] == [
            "HTTP_RESPONSE_RECEIVED", "RUN_SUCCEEDED"]
        all_rows = queued["items"] + latest["items"]
        ids = [row["id"] for row in all_rows]
        assert ids == sorted(set(ids))
        assert all(datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).tzinfo
                   == timezone.utc for row in all_rows)
        assert all(row["level"] in {"INFO", "WARN", "ERROR"}
                   and len(row["message"]) <= MAX_MESSAGE_LENGTH for row in all_rows)
        assert poll(client, alice, run_id, latest["next_after_id"]) == {
            "items": [], "next_after_id": latest["next_after_id"]}
        assert client.delete(f"/api/v1/workers/{worker_id}", headers=alice).status_code == 204
        assert poll(client, alice, run_id)["items"] == all_rows
        for headers in ({}, {"Authorization": "Bearer invalid"}):
            assert client.get(f"/api/v1/runs/{run_id}/logs", headers=headers).status_code == 401
        assert client.get(f"/api/v1/runs/{run_id}/logs", headers=bob).status_code == 404
        assert client.get(f"/api/v1/runs/{run_id}", headers=bob).status_code == 404
    # A new API and worker process see the same committed rows and cursor.
    with client_for(path) as reopened:
        assert poll(reopened, alice, run_id)["items"] == all_rows
        assert poll(reopened, alice, run_id, latest["next_after_id"])["items"] == []
        with WorkerRunner(f"sqlite:///{path}") as runner:
            assert not runner.run_once()
        assert poll(reopened, alice, run_id)["items"] == all_rows


def test_log_writer_rejects_untrusted_event_message_and_level():
    row = new_run_log("run", "owner", "2026-01-01T00:00:00Z", "INJECTED",
                      f"SECRET_{SENTINEL}", SENTINEL * 100)
    assert (row.level, row.event, row.message) == (
        "ERROR", "HANDLER_ERROR", "Worker handler failed")
    assert len(row.message) <= MAX_MESSAGE_LENGTH
    assert new_run_log("run", "owner", "2026-01-01T00:00:00Z", "INFO",
                       "HTTP_RESPONSE_RECEIVED", f"Received HTTP 500 {SENTINEL}").message == (
                           "HTTP response received")


@pytest.mark.parametrize("failure", ["fetch", "extraction", "llm"])
def test_summarizer_failure_logs_are_safe_and_terminal(tmp_path, monkeypatch, failure):
    path = tmp_path / f"{failure}.db"
    monkeypatch.setenv("LLM_MODEL", "test/model")

    def fetch(_url):
        if failure == "fetch":
            raise RuntimeError(SENTINEL)
        content_type = "application/json" if failure == "extraction" else "text/html"
        return outbound.FetchResponse(200, {"Content-Type": content_type},
                                      (f"<p>{SENTINEL} " + "readable words " * 20 + "</p>").encode())

    def llm(_self, **_kwargs):
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(outbound, "fetch", fetch)
    monkeypatch.setattr(LiteLLMClient, "complete", llm)
    with client_for(path) as client:
        alice = register(client, f"{failure}@example.com")
        _, run_id = enqueue(client, alice, "url_summarizer")
        with WorkerRunner(f"sqlite:///{path}") as runner:
            assert runner.run_once()
        response = poll(client, alice, run_id)
        rows = response["items"]
        events = [row["event"] for row in rows]
        assert events[:4] == ["RUN_QUEUED", "RUN_CLAIMED", "RUN_STARTED",
                              "URL_VALIDATION_STARTED"]
        assert events[-1] == "RUN_FAILED"
        assert {"fetch": "HTTP_REQUEST_FAILED", "extraction": "UNSUPPORTED_CONTENT_TYPE",
                "llm": "LLM_REQUEST_FAILED"}[failure] in events
        if failure == "extraction":
            assert "TEXT_EXTRACTION_FAILED" in events
        assert SENTINEL not in json.dumps(rows)
        assert all(len(row["message"]) <= MAX_MESSAGE_LENGTH for row in rows)
        with client.app.state.users.sessions() as session:
            stored = session.scalars(select(RunLogRecord).where(RunLogRecord.run_id == run_id)).all()
            assert SENTINEL not in json.dumps([(row.event, row.message) for row in stored])


def test_summarizer_success_logs_every_step(tmp_path, monkeypatch):
    path = tmp_path / "success.db"
    monkeypatch.setenv("LLM_MODEL", "test/model")
    monkeypatch.setattr(outbound, "fetch", lambda _url: outbound.FetchResponse(
        200, {"Content-Type": "text/html"}, b"<p>" + b"readable evidence " * 20 + b"</p>"))
    monkeypatch.setattr(LiteLLMClient, "complete", lambda _self, **_kwargs: "# Summary\n- Fact")
    with client_for(path) as client:
        alice = register(client, "summary@example.com")
        _, run_id = enqueue(client, alice, "url_summarizer")
        with WorkerRunner(f"sqlite:///{path}") as runner:
            assert runner.run_once()
        events = [row["event"] for row in poll(client, alice, run_id)["items"]]
        assert events == ["RUN_QUEUED", "RUN_CLAIMED", "RUN_STARTED",
                          "URL_VALIDATION_STARTED", "HTTP_REQUEST_STARTED",
                          "HTTP_RESPONSE_RECEIVED", "TEXT_EXTRACTION_STARTED", "TEXT_EXTRACTED",
                          "LLM_REQUEST_STARTED", "LLM_RESPONSE_RECEIVED", "SUMMARY_SAVED",
                          "RUN_SUCCEEDED"]
