"""Full API/worker/alert journeys on an isolated real PostgreSQL schema.

Run with RUNGUARD_TEST_POSTGRES_URL set to a disposable PostgreSQL database.
Outbound HTTP and the model are always fakes; this suite never calls a site or
spends model tokens.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import outbound
from app.main import create_app
from app.provider_config import DEMO_LABEL, DEMO_MODEL
from app.workers.host_model import HostCLIClient
from app.workers.llm import LiteLLMClient
from app.workers.runner import WorkerAlreadyRunning, WorkerRunner
from app.workers.url_summarizer import summarize
from app.workers.website_monitor import monitor


API = "/api/v1"
SECRET = "postgres-journey-test-secret-32-bytes-minimum"
PASSWORD = "CorrectHorse123!"


@pytest.fixture
def postgres_url(monkeypatch):
    source = os.environ.get("RUNGUARD_TEST_POSTGRES_URL")
    if not source:
        pytest.skip("RUNGUARD_TEST_POSTGRES_URL is not configured")
    url = make_url(source)
    if url.get_backend_name() != "postgresql":
        pytest.fail("RUNGUARD_TEST_POSTGRES_URL must use PostgreSQL")
    schema = f"runguard_journey_{uuid4().hex}"
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
    response = client.post(f"{API}/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _worker(client, headers, kind, name):
    response = client.post(f"{API}/workers", headers=headers, json={
        "name": name, "kind": kind, "url": f"https://example.com/{name.lower()}"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _enqueue(client, headers, worker_id):
    response = client.post(f"{API}/workers/{worker_id}/runs", headers=headers)
    assert response.status_code == 202, response.text
    return response.json()["run_id"]


def _run(client, headers, run_id):
    response = client.get(f"{API}/runs/{run_id}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def _logs(client, headers, run_id):
    response = client.get(f"{API}/runs/{run_id}/logs", headers=headers)
    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    assert [row["id"] for row in rows] == sorted(row["id"] for row in rows)
    return rows


def test_postgres_fresh_account_uses_no_key_extractive_demo(postgres_url, monkeypatch):
    monkeypatch.setenv("RUNGUARD_MODEL_PROVIDER", "disabled")
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    page = outbound.FetchResponse(200, {"Content-Type": "text/html"},
        b"<title>River report</title><article><p>" +
        b"The river rose overnight. Residents moved to higher ground. " +
        b"Officials monitored the roads and shared a new update. " * 2 +
        b"</p></article>")
    monkeypatch.setattr(outbound, "fetch", lambda _url: page)
    def forbidden_model(*_args, **_kwargs):
        raise AssertionError("The no-key demo must not call a model")
    monkeypatch.setattr(HostCLIClient, "complete", forbidden_model)
    monkeypatch.setattr(LiteLLMClient, "complete", forbidden_model)
    with TestClient(create_app(secret=SECRET, database_url=postgres_url)) as client:
        alice = _register(client, "fresh-demo@example.com")
        worker_id = _worker(client, alice, "url_summarizer", "Demo")
        with WorkerRunner(postgres_url) as runner:
            selected = client.get(f"{API}/ai/settings", headers=alice).json()
            assert selected == {"provider_path": "local_demo", "provider": "extractive",
                                "model": DEMO_MODEL, "available": True}
            run_id = _enqueue(client, alice, worker_id)
            assert runner.run_once() is True
            result = _run(client, alice, run_id)
            assert result["status"] == "succeeded"
            assert result["result"]["model"] == DEMO_LABEL
            assert result["result"]["summary"].startswith("- The river rose overnight.")
            events = [item["event"] for item in _logs(client, alice, run_id)]
            assert "DEMO_EXTRACTIVE_STARTED" in events
            assert "DEMO_EXTRACTIVE_COMPLETED" in events
            assert "LLM_REQUEST_STARTED" not in events


def test_postgres_full_journey_two_workers_alert_retry_and_restart(postgres_url):
    calls = []
    # A CI runner has no host CLI login. Advertise the injected fake model as
    # an available worker capability so enqueue still exercises the real gate.
    fake_capabilities = lambda: {"providers": ["codex"]}
    page = outbound.FetchResponse(200, {"Content-Type": "text/html"},
        b"<title>Recovery report</title><article><p>" +
        b"Operators detected the issue, restored service, and reviewed impact. " * 3 +
        b"</p></article>")

    class FakeModel:
        def complete(self, **kwargs):
            calls.append(kwargs)
            return "# Recovery report\n- Service was restored.\n- Impact was reviewed."

    with TestClient(create_app(secret=SECRET, database_url=postgres_url)) as client:
        alice = _register(client, "alice@example.com")
        bob = _register(client, "bob@example.com")
        monitor_id = _worker(client, alice, "website_monitor", "Monitor")
        summary_id = _worker(client, alice, "url_summarizer", "Summary")
        first_id = _enqueue(client, alice, monitor_id)
        assert _run(client, alice, first_id)["status"] == "queued"
        assert [row["event"] for row in _logs(client, alice, first_id)] == ["RUN_QUEUED"]
        for path in (f"/workers/{monitor_id}", f"/runs/{first_id}",
                     f"/runs/{first_id}/logs"):
            assert client.get(API + path, headers=bob).status_code == 404
        assert client.get(f"{API}/workers", headers=bob).json()["items"] == []
        assert client.get(f"{API}/runs", headers=bob).json()["items"] == []

        def fail_monitor(snapshot):
            return monitor(snapshot, fetch=lambda _url: outbound.FetchResponse(503, {}, b"down"))

        with WorkerRunner(postgres_url, {"website_monitor": fail_monitor},
                          capability_reporter=fake_capabilities) as runner:
            assert runner.run_once()
        assert _run(client, alice, first_id)["error"]["code"] == "HTTP_STATUS_ERROR"
        second_id = _enqueue(client, alice, monitor_id)
        with WorkerRunner(postgres_url, {"website_monitor": fail_monitor},
                          capability_reporter=fake_capabilities) as runner:
            assert runner.run_once()
        assert _run(client, alice, second_id)["status"] == "failed"
        alerts = client.get(f"{API}/alerts", headers=alice)
        assert alerts.status_code == 200, alerts.text
        assert alerts.json()["total"] == 1
        assert alerts.json()["items"][0]["kind"] == "repeated_run_failure"
        assert alerts.json()["items"][0]["run_id"] == second_id
        assert client.get(f"{API}/alerts", headers=bob).json()["total"] == 0

        retry_response = client.post(f"{API}/runs/{first_id}/retry", headers=alice)
        assert retry_response.status_code == 202, retry_response.text
        retry_id = retry_response.json()["run_id"]
        assert _run(client, alice, retry_id)["retry_of_run_id"] == first_id
        with WorkerRunner(postgres_url, {"website_monitor": lambda snapshot:
                monitor(snapshot, fetch=lambda _url: outbound.FetchResponse(200, {}, b"ok"))},
                capability_reporter=fake_capabilities) as runner:
            assert runner.run_once()
        assert _run(client, alice, retry_id)["status"] == "succeeded"
        assert _logs(client, alice, retry_id)[-1]["event"] == "RUN_SUCCEEDED"
        assert client.get(f"{API}/alerts", headers=alice).json()["items"][0]["status"] == "resolved"

        summary_run = _enqueue(client, alice, summary_id)
        with WorkerRunner(postgres_url, capability_reporter=fake_capabilities) as runner:
            runner.handlers["url_summarizer"] = lambda snapshot: summarize(
                snapshot, fetch=lambda _url: page, llm=FakeModel(), model="fake/model",
                log=lambda event, message, level: runner._monitor_step(
                    snapshot.id, event, message, level))
            assert runner.run_once()
        saved = _run(client, alice, summary_run)
        assert saved["status"] == "succeeded"
        assert saved["result"]["page_title"] == "Recovery report"
        assert saved["result"]["model"] == "fake/model"
        assert len(calls) == 1
        assert [row["event"] for row in _logs(client, alice, summary_run)].index("LLM_REQUEST_STARTED") < [
            row["event"] for row in _logs(client, alice, summary_run)].index("SUMMARY_SAVED")
        assert client.get(f"{API}/dashboard/summary", headers=alice).status_code == 200

    with TestClient(create_app(secret=SECRET, database_url=postgres_url)) as restarted:
        login = restarted.post(f"{API}/auth/login", json={
            "email": "alice@example.com", "password": PASSWORD})
        assert login.status_code == 200, login.text
        alice = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert [_run(restarted, alice, run_id)["status"] for run_id in (
            first_id, second_id, retry_id, summary_run)] == [
                "failed", "failed", "succeeded", "succeeded"]
        assert restarted.get(f"{API}/alerts", headers=alice).json()["items"][0]["status"] == "resolved"
        assert all(_logs(restarted, alice, run_id)[-1]["event"].startswith("RUN_")
                   for run_id in (first_id, second_id, retry_id, summary_run))


def test_postgres_concurrent_enqueue_single_claim_and_tenant_isolation(postgres_url):
    with TestClient(create_app(secret=SECRET, database_url=postgres_url)) as first, TestClient(
            create_app(secret=SECRET, database_url=postgres_url)) as second:
        alice = _register(first, "alice@example.com")
        bob = _register(first, "bob@example.com")
        worker_id = _worker(first, alice, "website_monitor", "Race")
        gate = Barrier(2)

        def launch(client):
            gate.wait(timeout=10)
            return client.post(f"{API}/workers/{worker_id}/runs", headers=alice)

        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(launch, first)
            two = pool.submit(launch, second)
            responses = [one.result(timeout=20), two.result(timeout=20)]
        assert sorted(response.status_code for response in responses) == [202, 409]
        run_id = next(response.json()["run_id"] for response in responses
                      if response.status_code == 202)
        assert second.post(f"{API}/workers/{worker_id}/runs", headers=bob).status_code == 404
        assert second.get(f"{API}/runs/{run_id}", headers=bob).status_code == 404
        handled = []

        def fake_handler(snapshot):
            handled.append(snapshot.id)
            return monitor(snapshot, fetch=lambda _url: outbound.FetchResponse(200, {}, b"ok"))

        with WorkerRunner(postgres_url, {"website_monitor": fake_handler}) as runner:
            with pytest.raises(WorkerAlreadyRunning):
                with WorkerRunner(postgres_url, {"website_monitor": fake_handler}):
                    pass
            assert runner.run_once()
            assert not runner.run_once()
        assert handled == [run_id]
        assert _run(first, alice, run_id)["status"] == "succeeded"
        assert [row["event"] for row in _logs(first, alice, run_id)].count("RUN_CLAIMED") == 1
        assert first.get(f"{API}/runs", headers=bob).json()["total"] == 0
