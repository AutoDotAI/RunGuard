"""Offline checks for correlated, bounded user-journey telemetry."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import yaml
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import select

from app import observability
from app.db import RunRecord
from app.main import create_app
from app.workers.runner import HandlerOutcome, WorkerRunner
from app.workers.url_summarizer import summarize
from app.workers.website_monitor import monitor
from app.outbound import FetchResponse


SECRET = "test-only-signing-secret-32-bytes-minimum"


def _events(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()
            if line.startswith("{") and '"service":"runguard-' in line]


def test_api_to_worker_trace_and_sanitized_json_logs(tmp_path, capsys):
    database_url = f"sqlite:///{tmp_path / 'journey.db'}"
    exporter = InMemorySpanExporter()
    with TestClient(create_app(secret=SECRET, database_url=database_url)) as client:
        trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
        email = "private-person@example.test"
        password = "secret-test-password-123"
        registration = client.post("/api/v1/auth/register", json={"email": email, "password": password})
        assert registration.status_code == 201
        token = registration.json()["access_token"]
        header = {"Authorization": f"Bearer {token}"}
        worker = client.post("/api/v1/workers", headers=header, json={
            "name": "Private page", "kind": "website_monitor",
            "url": "https://example.com/path?private_secret=do-not-log"})
        assert worker.status_code == 201
        enqueued = client.post(f"/api/v1/workers/{worker.json()['id']}/runs", headers=header)
        assert enqueued.status_code == 202
        run_id = enqueued.json()["run_id"]
        request_id = enqueued.headers["X-Request-ID"]
        with client.app.state.users.sessions() as session:
            queued = session.scalar(select(RunRecord).where(RunRecord.id == run_id))
            assert queued.traceparent and queued.trace_id
            expected_trace_id = queued.trace_id

        def fake_monitor(_snapshot):
            return monitor(_snapshot, fetch=lambda _url: FetchResponse(200, {}, b""))

        with WorkerRunner(database_url, {"website_monitor": fake_monitor}) as runner:
            assert runner.run_once() is True
        assert client.get(f"/api/v1/runs/{run_id}", headers=header).json()["status"] == "succeeded"
        bad = client.post("/api/v1/auth/login", json={"email": email, "password": "wrong-password"})
        assert bad.status_code == 401
        assert bad.json()["request_id"] == bad.headers["X-Request-ID"]

    spans = exporter.get_finished_spans()
    names = {span.name for span in spans if format(span.context.trace_id, "032x") == expected_trace_id}
    assert {"http.request", "db.enqueue", "worker.claim", "worker.execute", "http.fetch", "db.result"} <= names
    events = _events(capsys)
    assert any(event["event"] == "RUN_QUEUED" and event["run_id"] == run_id
               and event["request_id"] == request_id and event["trace_id"] == expected_trace_id
               for event in events)
    assert any(event["event"] == "RUN_SUCCEEDED" and event["run_id"] == run_id
               and event["trace_id"] == expected_trace_id for event in events)
    assert any(event["event"] == "LOGIN_FAILED" and event["request_id"] == bad.headers["X-Request-ID"]
               for event in events)
    serialized = json.dumps(events)
    for private in (email, password, token, "private_secret", "do-not-log"):
        assert private not in serialized
        assert private not in str([span.attributes for span in spans])
    assert all(set(event) == {"timestamp", "level", "severity", "service", "event", "message",
                              "request_id", "run_id", "trace_id"} for event in events)


class _MeterCapture:
    def __init__(self):
        self.calls = []

    def add(self, value, attributes):
        self.calls.append(("add", value, attributes))

    def record(self, value, attributes):
        self.calls.append(("record", value, attributes))


def test_metric_recording_has_fixed_labels_and_seconds(monkeypatch):
    instruments = {name: _MeterCapture() for name in
                   ("runs_enqueued", "runs_started", "runs_completed", "duration", "queue_wait", "api_outcomes")}
    monkeypatch.setattr(observability, "_runtime", instruments)
    observability.record_enqueue("website_monitor")
    observability.record_start("website_monitor", 1.25)
    observability.record_completion("website_monitor", "failed", 0.5)
    observability.record_api_outcome("register", 201)
    observability.record_api_outcome("enqueue", 409)
    observability.record_enqueue("user-controlled-kind")
    assert instruments["runs_enqueued"].calls == [("add", 1, {"kind": "website_monitor"})]
    assert instruments["runs_started"].calls == [("add", 1, {"kind": "website_monitor"})]
    assert instruments["queue_wait"].calls == [("record", 1.25, {"kind": "website_monitor"})]
    assert instruments["runs_completed"].calls == [
        ("add", 1, {"kind": "website_monitor", "status": "failed"})]
    assert instruments["duration"].calls == [("record", 0.5, {"kind": "website_monitor"})]
    assert instruments["api_outcomes"].calls == [
        ("add", 1, {"operation": "register", "outcome": "success", "status_class": "2xx"}),
        ("add", 1, {"operation": "enqueue", "outcome": "client_error", "status_class": "4xx"}),
    ]


def test_heartbeat_age_grows_after_worker_stops(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'heartbeat.db'}"
    with WorkerRunner(database_url) as runner:
        before = runner.lock_path.stat().st_mtime
        assert before > 0
    monkeypatch.setattr(observability, "_runtime", {"database_url": database_url})
    options = SimpleNamespace(timeout_millis=1000)
    first = list(observability._heartbeat_age(options))[0].value
    time.sleep(0.02)
    later = list(observability._heartbeat_age(options))[0].value
    assert later > first >= 0
    assert sorted(observation.attributes["kind"] for observation in observability._queue_depth(options)) == [
        "url_summarizer", "website_monitor"]


def test_sqlite_traceparent_survives_a_separate_worker_process(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'separate.db'}"
    with TestClient(create_app(secret=SECRET, database_url=database_url)) as client:
        token = client.post("/api/v1/auth/register", json={
            "email": "separate@example.test", "password": "password-12345"}).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        worker = client.post("/api/v1/workers", headers=headers, json={
            "name": "Separate", "kind": "website_monitor", "url": "https://example.com/"}).json()
        run_id = client.post(f"/api/v1/workers/{worker['id']}/runs", headers=headers).json()["run_id"]
        with client.app.state.users.sessions() as session:
            saved = session.get(RunRecord, run_id)
            expected = saved.trace_id
            assert expected and saved.traceparent
    script = """from app.workers.runner import WorkerRunner
from app.workers.website_monitor import monitor
from app.outbound import FetchResponse
with WorkerRunner(handlers={'website_monitor': lambda snapshot: monitor(
        snapshot, fetch=lambda _url: FetchResponse(200, {}, b''))}) as runner:
    assert runner.run_once()
"""
    process = subprocess.run([sys.executable, "-c", script], env={
        **os.environ, "RUNGUARD_DATABASE_URL": database_url, "RUNGUARD_OTEL_ENDPOINT": ""},
        capture_output=True, text=True, timeout=15, check=True)
    events = [json.loads(line) for line in process.stdout.splitlines() if line.startswith("{")]
    assert any(event["event"] == "RUN_SUCCEEDED" and event["run_id"] == run_id
               and event["trace_id"] == expected for event in events)


def test_summarizer_creates_fetch_and_model_spans_without_content():
    exporter = InMemorySpanExporter()
    observability.configure("runguard-api")
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    body = b"<html><body><article><p>" + b"A factual paragraph about a local river. " * 5 + b"</p></article></body></html>"

    class FakeModel:
        def complete(self, **_kwargs):
            return "# River\n- The local river was described."

    from app.workers.types import RunSnapshot
    with observability.tracer().start_as_current_span("worker.execute"):
        outcome = summarize(RunSnapshot("run-id", "url_summarizer", "https://example.com/", "{}"),
                            fetch=lambda _url: FetchResponse(200, {"Content-Type": "text/html"}, body),
                            llm=FakeModel(), model="test/model")
    assert outcome.error_code is None
    spans = exporter.get_finished_spans()
    assert {"worker.execute", "http.fetch", "llm.summarize"} <= {span.name for span in spans}
    serialized = str([span.attributes for span in spans])
    assert "A factual paragraph" not in serialized
    assert "https://example.com/" not in serialized


def test_reconciliation_emits_correlated_restart_events(tmp_path, capsys):
    database_url = f"sqlite:///{tmp_path / 'restart.db'}"
    with TestClient(create_app(secret=SECRET, database_url=database_url)) as client:
        token = client.post("/api/v1/auth/register", json={
            "email": "restart@example.test", "password": "password-12345"}).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        worker = client.post("/api/v1/workers", headers=headers, json={
            "name": "Restart", "kind": "website_monitor", "url": "https://example.com/"}).json()
        run_id = client.post(f"/api/v1/workers/{worker['id']}/runs", headers=headers).json()["run_id"]
        with WorkerRunner(database_url) as runner:
            assert runner.claim_oldest().id == run_id
        with WorkerRunner(database_url):
            pass
        assert client.get(f"/api/v1/runs/{run_id}", headers=headers).json()["status"] == "failed"
    events = _events(capsys)
    assert any(event["event"] == "WORKER_INTERRUPTED" and event["run_id"] == run_id
               and event["trace_id"] for event in events)
    assert any(event["event"] == "WORKER_RECONCILED" for event in events)


def test_collector_and_dashboard_are_local_only_and_use_journey_signals():
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "compose.observability.yaml").read_text())
    collector = yaml.safe_load((root / "observability/collector.yaml").read_text())
    dashboard = json.loads((root / "observability/dashboards/runguard.json").read_text())
    assert compose["services"]["observability"]["ports"] == ["127.0.0.1:3001:3000"]
    assert "ports" not in compose["services"]["collector"]
    assert compose["services"]["api"]["environment"]["RUNGUARD_OTEL_ENDPOINT"] == "http://collector:4318"
    assert set(collector["service"]["pipelines"]) == {"traces", "metrics", "logs"}
    queries = " ".join(target["expr"] for panel in dashboard["panels"] for target in panel.get("targets", []))
    for metric in ("runguard_runs_completed_total", "runguard_queue_wait_seconds_bucket",
                   "runguard_worker_heartbeat_age_seconds", "runguard_api_outcomes_total"):
        assert metric in queries
    assert "host_cpu" not in queries
