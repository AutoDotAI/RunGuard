"""Offline API -> migrated SQLite -> separate worker -> API journeys.

Run from a fresh checkout with ``cd backend && uv sync --extra test &&
uv run pytest -q tests/integration``. Each test owns a temporary SQLite file;
only outbound website and model clients are faked.
"""

import json
import sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app import outbound
from app.db import RunRecord, WorkerRecord
from app.main import create_app
from app.workers.runner import WorkerRunner
from app.workers.url_summarizer import summarize
from app.workers.website_monitor import monitor


def migration_head() -> str:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    return ScriptDirectory.from_config(config).get_current_head()


SECRET = "integration-test-signing-secret-at-least-32-bytes"
PASSWORD = "CorrectHorse123!"
API = "/api/v1"


@pytest.fixture
def database_url(tmp_path):
    return f"sqlite:///{tmp_path / 'journey.db'}"


@pytest.fixture
def client(database_url):
    with TestClient(create_app(secret=SECRET, database_url=database_url)) as client:
        client.app.state.ai_settings.capability_reader = lambda: {"codex"}
        with sqlite3.connect(database_url.removeprefix("sqlite:///")) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == migration_head()
        yield client


def register(client, email):
    response = client.post(f"{API}/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 201, response.text
    assert response.json()["token_type"] == "bearer"
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_worker(client, headers, kind="website_monitor", name="Original", url="https://example.com/check"):
    response = client.post(f"{API}/workers", headers=headers,
                           json={"name": name, "kind": kind, "url": url})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def enqueue(client, headers, worker_id):
    response = client.post(f"{API}/workers/{worker_id}/runs", headers=headers)
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    return response.json()["run_id"]


def run(client, headers, run_id):
    response = client.get(f"{API}/runs/{run_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def logs(client, headers, run_id):
    response = client.get(f"{API}/runs/{run_id}/logs", headers=headers)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["id"] for item in items] == sorted(item["id"] for item in items)
    assert all(datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")).tzinfo
               == timezone.utc for item in items)
    return items


def test_monitor_api_restart_failure_retry_snapshot_and_archive(client, database_url):
    register(client, "alice@example.com")
    login = client.post(f"{API}/auth/login", json={"email": "alice@example.com", "password": PASSWORD})
    assert login.status_code == 200
    assert login.json()["user"]["email"] == "alice@example.com"
    alice = {"Authorization": f"Bearer {login.json()['access_token']}"}
    worker_id = create_worker(client, alice)
    with client.app.state.users.sessions() as session:
        session.get(WorkerRecord, worker_id).config_json = '{"version":1}'
        session.commit()
    source_id = enqueue(client, alice, worker_id)
    assert run(client, alice, source_id)["status"] == "queued"
    assert [item["event"] for item in logs(client, alice, source_id)] == ["RUN_QUEUED"]

    # API construction and HTTP enqueue do not call the outbound handler.
    fetched = []
    def failing_fetch(url):
        fetched.append(url)
        raise outbound.OutboundError("FETCH_TIMEOUT", "Destination request timed out")

    with TestClient(create_app(secret=SECRET, database_url=database_url)) as restarted:
        assert run(restarted, alice, source_id)["status"] == "queued"
        persisted_worker = restarted.get(f"{API}/workers/{worker_id}", headers=alice)
        assert persisted_worker.status_code == 200
        assert {key: persisted_worker.json()[key] for key in ("id", "name", "kind", "url")} == {
            "id": worker_id, "name": "Original", "kind": "website_monitor",
            "url": "https://example.com/check"}
        assert [item["id"] for item in restarted.get(f"{API}/workers", headers=alice).json()["items"]] == [worker_id]
        assert fetched == []
        with WorkerRunner(database_url, {"website_monitor": lambda snapshot:
                          monitor(snapshot, fetch=failing_fetch)}) as runner:
            assert runner.run_once() is True
            assert runner.run_once() is False
        failed = run(restarted, alice, source_id)
        assert failed["status"] == "failed"
        assert failed["result"] is None
        assert failed["error"] == {"code": "FETCH_TIMEOUT", "message": "Destination request timed out"}
        failed_logs = logs(restarted, alice, source_id)
        assert [item["event"] for item in failed_logs][-2:] == ["FETCH_TIMEOUT", "RUN_FAILED"]
        assert fetched == ["https://example.com/check"]
        assert restarted.get(f"{API}/runs", headers=alice).json()["total"] == 1

        edited = restarted.patch(f"{API}/workers/{worker_id}", headers=alice,
                                 json={"name": "Changed", "url": "https://example.org/changed"})
        assert edited.status_code == 200
        with restarted.app.state.users.sessions() as session:
            session.get(WorkerRecord, worker_id).config_json = '{"version":2}'
            session.commit()
        retried = restarted.post(f"{API}/runs/{source_id}/retry", headers=alice)
        assert retried.status_code == 202
        retry_id = retried.json()["run_id"]
        assert retry_id != source_id
        retry = run(restarted, alice, retry_id)
        assert (retry["worker_name"], retry["url"], retry["retry_of_run_id"], retry["status"]) == (
            "Original", "https://example.com/check", source_id, "queued")
        with restarted.app.state.users.sessions() as session:
            assert json.loads(session.get(RunRecord, retry_id).config_snapshot_json) == {"version": 1}
        assert run(restarted, alice, source_id) == failed
        assert logs(restarted, alice, source_id) == failed_logs

        with WorkerRunner(database_url, {"website_monitor": lambda snapshot:
                          monitor(snapshot, fetch=lambda url: outbound.FetchResponse(200, {}, b"ok"))}) as runner:
            assert runner.run_once() is True
            assert runner.run_once() is False
        succeeded = run(restarted, alice, retry_id)
        assert succeeded["status"] == "succeeded"
        assert succeeded["result"]["healthy"] is True
        assert succeeded["result"]["http_status"] == 200
        assert [row["event"] for row in logs(restarted, alice, retry_id)][-1] == "RUN_SUCCEEDED"
        assert restarted.delete(f"{API}/workers/{worker_id}", headers=alice).status_code == 204

    with TestClient(create_app(secret=SECRET, database_url=database_url)) as reopened:
        assert reopened.get(f"{API}/workers", headers=alice).json()["items"] == []
        original_snapshot = run(reopened, alice, source_id)
        retry_snapshot = run(reopened, alice, retry_id)
        assert original_snapshot == failed
        assert retry_snapshot == succeeded
        assert (original_snapshot["worker_name"], original_snapshot["kind"], original_snapshot["url"]) == (
            "Original", "website_monitor", "https://example.com/check")
        assert (retry_snapshot["worker_name"], retry_snapshot["kind"], retry_snapshot["url"],
                retry_snapshot["retry_of_run_id"]) == (
            "Original", "website_monitor", "https://example.com/check", source_id)
        with reopened.app.state.users.sessions() as session:
            assert json.loads(session.get(RunRecord, source_id).config_snapshot_json) == {"version": 1}
            assert json.loads(session.get(RunRecord, retry_id).config_snapshot_json) == {"version": 1}
        assert logs(reopened, alice, source_id) == failed_logs
        assert reopened.get(f"{API}/runs", headers=alice).json()["total"] == 2


def test_summarizer_api_worker_result_and_provider_failure(client, database_url):
    alice = register(client, "summary@example.com")
    worker_id = create_worker(client, alice, "url_summarizer", "Article", "https://example.com/article")
    page = outbound.FetchResponse(200, {"Content-Type": "text/html"},
        b"<title>Incident report</title><article><p>" +
        b"The report describes an incident timeline, the response, and the recovery steps. " * 3 +
        b"</p></article>")
    calls = []
    expected_summary = "# Incident report\n- Response began.\n- Recovery followed.\n- Impact was reviewed."

    class FakeLLM:
        def __init__(self, fail=False):
            self.fail = fail

        def complete(self, **kwargs):
            calls.append(kwargs)
            if self.fail:
                raise RuntimeError("private-provider-key and page text")
            return expected_summary

    def handler(runner, fail=False):
        return lambda snapshot: summarize(snapshot, fetch=lambda url: page,
            llm=FakeLLM(fail), model="test/provider-model",
            log=lambda event, message, level: runner._monitor_step(snapshot.id, event, message, level))

    first_id = enqueue(client, alice, worker_id)
    with WorkerRunner(database_url) as runner:
        runner.handlers["url_summarizer"] = handler(runner)
        assert runner.run_once() is True
    success = run(client, alice, first_id)
    assert success["status"] == "succeeded"
    assert success["result"] == {"source_url": "https://example.com/article",
        "page_title": "Incident report", "summary": expected_summary,
        "model": "test/provider-model", "input_was_truncated": False}
    events = [item["event"] for item in logs(client, alice, first_id)]
    assert events.index("TEXT_EXTRACTED") < events.index("LLM_REQUEST_STARTED") < events.index("SUMMARY_SAVED")
    assert len(calls) == 1

    second_id = enqueue(client, alice, worker_id)
    with WorkerRunner(database_url) as runner:
        runner.handlers["url_summarizer"] = handler(runner, fail=True)
        assert runner.run_once() is True
        assert runner.run_once() is False
    failed = run(client, alice, second_id)
    assert failed["status"] == "failed" and failed["result"] is None
    assert failed["error"]["code"] == "LLM_ERROR"
    assert "private-provider-key" not in json.dumps(failed) + json.dumps(logs(client, alice, second_id))
    assert len(calls) == 2  # one provider call per run, no SDK retry or fallback


def test_two_user_private_reads_mutations_and_lists(client):
    alice = register(client, "alice@example.com")
    bob = register(client, "bob@example.com")
    worker_id = create_worker(client, alice)
    run_id = enqueue(client, alice, worker_id)
    assert client.get(f"{API}/workers", headers=bob).json()["items"] == []
    assert client.get(f"{API}/runs", headers=bob).json()["items"] == []
    assert client.get(f"{API}/dashboard/summary", headers=bob).json()["recent_runs"] == []
    for method, suffix, body in [
        ("get", f"/workers/{worker_id}", None),
        ("patch", f"/workers/{worker_id}", {"name": "Stolen"}),
        ("delete", f"/workers/{worker_id}", None),
        ("post", f"/workers/{worker_id}/runs", None),
        ("get", f"/runs/{run_id}", None),
        ("get", f"/runs/{run_id}/logs", None),
        ("post", f"/runs/{run_id}/retry", None),
    ]:
        response = getattr(client, method)(API + suffix, headers=bob, **({"json": body} if body else {}))
        assert response.status_code == 404, (method, suffix, response.text)
    assert client.get(f"{API}/runs", headers=bob, params={"worker_id": worker_id}).status_code == 404
    assert client.get(f"{API}/workers/{worker_id}", headers=alice).json()["name"] == "Original"
    assert run(client, alice, run_id)["status"] == "queued"
    assert [item["event"] for item in logs(client, alice, run_id)] == ["RUN_QUEUED"]


def test_interrupted_worker_reconciles_without_reexecution(client, database_url):
    alice = register(client, "alice@example.com")
    interrupted_worker = create_worker(client, alice, name="Interrupted")
    queued_worker = create_worker(client, alice, name="Waiting")
    interrupted = enqueue(client, alice, interrupted_worker)
    waiting = enqueue(client, alice, queued_worker)

    def crash(_snapshot):
        raise KeyboardInterrupt("simulated process termination")

    with pytest.raises(KeyboardInterrupt):
        with WorkerRunner(database_url, {"website_monitor": crash}) as runner:
            runner.run_once()
    assert run(client, alice, interrupted)["status"] == "running"
    before = logs(client, alice, interrupted)
    assert [item["event"] for item in before][:3] == ["RUN_QUEUED", "RUN_CLAIMED", "RUN_STARTED"]
    seen = []
    with WorkerRunner(database_url, {"website_monitor": lambda snapshot:
                      seen.append(snapshot.id) or monitor(snapshot, fetch=lambda url:
                      outbound.FetchResponse(200, {}, b"ok"))}) as runner:
        recovered = run(client, alice, interrupted)
        assert recovered["status"] == "failed" and recovered["error"]["code"] == "WORKER_INTERRUPTED"
        assert [item["id"] for item in logs(client, alice, interrupted)][:len(before)] == [item["id"] for item in before]
        assert run(client, alice, waiting)["status"] == "queued"
        assert runner.run_once() is True
        assert runner.run_once() is False
    assert seen == [waiting]
    assert run(client, alice, waiting)["status"] == "succeeded"
    with WorkerRunner(database_url, {"website_monitor": lambda snapshot: pytest.fail("re-executed")}) as runner:
        assert runner.run_once() is False


def test_concurrent_run_now_one_active_row(client, database_url):
    alice = register(client, "alice@example.com")
    worker_id = create_worker(client, alice)
    barrier = Barrier(2)

    def submit():
        barrier.wait(timeout=10)
        return client.post(f"{API}/workers/{worker_id}/runs", headers=alice)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit) for _ in range(2)]
        responses = [future.result(timeout=20) for future in futures]
    assert sorted(response.status_code for response in responses) == [202, 409]
    accepted = next(response for response in responses if response.status_code == 202).json()["run_id"]
    rejected = next(response for response in responses if response.status_code == 409).json()
    assert rejected["error"]["code"] == "RUN_ALREADY_ACTIVE"
    assert rejected["error"]["details"] == {"run_id": accepted}
    with sqlite3.connect(database_url.removeprefix("sqlite:///")) as connection:
        rows = connection.execute("SELECT id, status FROM runs WHERE worker_id = ?", (worker_id,)).fetchall()
        assert rows == [(accepted, "queued")]
        assert connection.execute("SELECT COUNT(*) FROM run_logs WHERE run_id = ?", (accepted,)).fetchone()[0] == 1
