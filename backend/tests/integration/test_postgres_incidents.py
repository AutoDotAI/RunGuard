"""Real PostgreSQL alert, responder, demo, and metric regression checks.

RUNGUARD_TEST_POSTGRES_URL must point to a disposable database. Every test
creates and drops its own schema; no application or existing volume is used.
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from app import observability
from app.alerting import AlertService, stamp
from app.db import AlertRecord, IncidentRecord, ResponderActionRecord, RunRecord, UserRepository, WorkerRecord
from app.demo_worker import DemoControl, DemoDenied, DemoWorkerRunner
from app.incident_ai import IncidentAI
from app.responder_adapter import RunGuardResponder as Responder


NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def postgres_url():
    raw = os.environ.get("RUNGUARD_TEST_POSTGRES_URL")
    if not raw:
        pytest.skip("RUNGUARD_TEST_POSTGRES_URL is not configured")
    url = make_url(raw)
    if url.get_backend_name() != "postgresql":
        pytest.fail("RUNGUARD_TEST_POSTGRES_URL must use PostgreSQL")
    schema = f"rg_incident_{uuid4().hex}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        yield url.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(
            hide_password=False)
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def add_run(users, owner: str, status: str, *, finished=None, created=None) -> str:
    worker_id, run_id = str(uuid4()), str(uuid4())
    created = created or NOW
    with users.sessions() as session:
        session.add(WorkerRecord(id=worker_id, owner_user_id=owner, name="Fixture",
                                 kind="website_monitor", url="https://example.com/",
                                 config_json="{}", created_at=stamp(created),
                                 updated_at=stamp(created), archived_at=None))
        session.flush()
        session.add(RunRecord(id=run_id, worker_id=worker_id, owner_user_id=owner,
                              worker_name_snapshot="Fixture", kind="website_monitor",
                              url_snapshot="https://example.com/", config_snapshot_json="{}",
                              status=status, created_at=stamp(created),
                              started_at=stamp(created) if status != "queued" else None,
                              finished_at=stamp(finished) if finished else None,
                              duration_ms=1000 if finished else None, http_status=None,
                              result_json=None,
                              error_code="HANDLER_ERROR" if status == "failed" else None,
                              error_message="Handler failed" if status == "failed" else None,
                              retry_of_run_id=None, trace_id="a" * 32, traceparent=None))
        session.commit()
    return run_id


def test_alert_incident_ai_and_metrics_survive_postgres_restart(postgres_url, monkeypatch):
    users = UserRepository(postgres_url)
    users.migrate()
    alice = users.create("alice@example.test", "hashed-password", stamp(NOW))["id"]
    bob = users.create("bob@example.test", "hashed-password", stamp(NOW))["id"]
    add_run(users, alice, "failed", finished=NOW - timedelta(seconds=2))
    latest = add_run(users, alice, "failed", finished=NOW - timedelta(seconds=1))
    add_run(users, bob, "failed", finished=NOW - timedelta(seconds=1))
    service = AlertService(users, clock=lambda: NOW)
    assert service.evaluate()["repeated_run_failure"] == 1
    alert = service.list_alerts(alice).items[0]
    assert alert.run_id == latest and alert.status == "open"
    assert service.list_alerts(bob).total == 0
    assert Responder(users).respond(alert.id, bob) is None
    incident = Responder(users).respond(alert.id, alice)
    assert incident["action"]["decision"] == "action_denied"
    assert incident["action"]["action"] == "none"
    assert Responder(users).respond(alert.id, alice)["incident_id"] == incident["incident_id"]
    monkeypatch.setattr("app.incident_ai.shutil.which", lambda _: None)
    analysis = IncidentAI(users).investigate(incident["incident_id"], alice)
    assert analysis["status"] == "fallback" and analysis["failure_code"] == "CLI_MISSING"

    monkeypatch.setattr(observability, "_runtime", {"database_url": postgres_url})
    options = SimpleNamespace(timeout_millis=1000)
    metrics = list(observability._open_alerts(options))
    assert {item.attributes["kind"]: item.value for item in metrics}["repeated_run_failure"] == 1
    queue = list(observability._queue_depth(options))
    assert {item.attributes["kind"]: item.value for item in queue}["website_monitor"] == 0
    with users.sessions() as session:
        session.execute(text("INSERT INTO worker_heartbeat(id,last_seen_at,capabilities_json) "
                             "VALUES (1,:seen,'{}')"), {"seen": stamp(datetime.now(timezone.utc))})
        session.commit()
    assert list(observability._heartbeat_age(options))[0].value >= 0

    add_run(users, alice, "succeeded", finished=NOW)
    assert service.evaluate()["repeated_run_failure"] == 0
    users.close()
    reopened = UserRepository(postgres_url)
    try:
        with reopened.sessions() as session:
            assert session.scalar(select(AlertRecord).where(AlertRecord.id == alert.id)).status == "resolved"
            assert session.scalar(select(IncidentRecord).where(
                IncidentRecord.id == incident["incident_id"])).id == incident["incident_id"]
            assert session.scalar(select(ResponderActionRecord).where(
                ResponderActionRecord.incident_id == incident["incident_id"])).action == "none"
        assert IncidentAI(reopened).review(incident["incident_id"], alice)["analysis_id"] == analysis["analysis_id"]
    finally:
        reopened.close()


def test_pg_demo_control_is_persisted_and_restricted(postgres_url, monkeypatch):
    monkeypatch.setenv("RUNGUARD_DEMO_MODE", "true")
    monkeypatch.setenv("RUNGUARD_DEMO_PROJECT", "rg-demo-postgres-test")
    monkeypatch.setenv("RUNGUARD_COMPOSE_PROJECT", "rg-demo-postgres-test")
    monkeypatch.delenv("RUNGUARD_PUBLIC_MODE", raising=False)
    users = UserRepository(postgres_url)
    users.migrate()
    owner = users.create("demo@example.test", "hashed-password", stamp(NOW))["id"]
    control = DemoControl(postgres_url)
    try:
        assert control.pause() == {"status": "paused", "run_id": None, "owner_id": None}
        run_id = add_run(users, owner, "queued", created=NOW)
        assert control.bind(run_id)["run_id"] == run_id
        assert control.resume()["status"] == "resumed"
        control.close()
        control = DemoControl(postgres_url)
        assert control.status()["status"] == "resumed"
        with pytest.raises(DemoDenied):
            control.resume(require_paused=True)
        with users.sessions() as session:
            run = session.get(RunRecord, run_id)
            run.status = "succeeded"
            run.finished_at = stamp(NOW)
            session.commit()
        assert control.cleanup() is None
        assert control.status() is None
    finally:
        control.close()
        users.close()


def test_external_responder_pg_demo_recovery_is_owned_audited_and_idempotent(
        postgres_url, monkeypatch):
    monkeypatch.setenv("RUNGUARD_DEMO_MODE", "true")
    monkeypatch.setenv("RUNGUARD_DEMO_PROJECT", "rg-demo-external-pg")
    monkeypatch.setenv("RUNGUARD_COMPOSE_PROJECT", "rg-demo-external-pg")
    monkeypatch.delenv("RUNGUARD_PUBLIC_MODE", raising=False)
    users = UserRepository(postgres_url)
    users.migrate()
    owner = users.create("owner@example.test", "hashed-password", stamp(NOW))["id"]
    other = users.create("other@example.test", "hashed-password", stamp(NOW))["id"]
    control = DemoControl(postgres_url)
    try:
        control.pause()
        run_id = add_run(users, owner, "queued",
                         created=datetime.now(timezone.utc) - timedelta(seconds=70))
        assert control.bind(run_id)["run_id"] == run_id
        assert AlertService(users).evaluate()["worker_queue_stalled"] == 1
        with users.sessions() as session:
            alert = session.scalar(select(AlertRecord).where(AlertRecord.run_id == run_id))
            assert alert is not None and alert.status == "open"
            alert_id = alert.id

        assert Responder(users).respond(alert_id, other) is None
        calls = []

        def advance(_delay):
            if not calls:
                with DemoWorkerRunner(postgres_url) as runner:
                    assert runner.run_once()
                calls.append(True)

        incident = Responder(users, recovery_timeout=30, sleep=advance).respond(alert_id, owner)
        assert incident["status"] == "recovered"
        assert incident["action"]["action"] == "resume_demo_worker"
        assert incident["action"]["decision"] == "executed"
        assert incident["action"]["result"] == "recovered"
        assert incident["action"]["before_status"] == "queued"
        assert incident["action"]["after_status"] == "succeeded"
        assert incident["evidence"]["inspection_names"] == [
            "get_worker_health", "get_failed_run", "get_recent_errors", "get_queue_depth"]
        assert incident["evidence"]["log_ids"]
        assert Responder(users).respond(alert_id, owner) == incident
        users.close()
        reopened = UserRepository(postgres_url)
        try:
            assert Responder(reopened).respond(alert_id, owner) == incident
            with reopened.sessions() as session:
                assert session.scalar(select(func.count()).select_from(IncidentRecord)) == 1
                assert session.scalar(select(func.count()).select_from(ResponderActionRecord)) == 1
                assert session.scalar(select(func.count()).select_from(RunRecord).where(
                    RunRecord.retry_of_run_id.is_not(None))) == 0
        finally:
            reopened.close()
    finally:
        control.cleanup()
        control.close()
        users.close()


def test_external_responder_pg_rejects_unbound_demo_run(postgres_url, monkeypatch):
    monkeypatch.setenv("RUNGUARD_DEMO_MODE", "true")
    monkeypatch.setenv("RUNGUARD_DEMO_PROJECT", "rg-demo-external-pg")
    monkeypatch.setenv("RUNGUARD_COMPOSE_PROJECT", "rg-demo-external-pg")
    monkeypatch.delenv("RUNGUARD_PUBLIC_MODE", raising=False)
    users = UserRepository(postgres_url)
    users.migrate()
    owner = users.create("owner@example.test", "hashed-password", stamp(NOW))["id"]
    other = users.create("other@example.test", "hashed-password", stamp(NOW))["id"]
    control = DemoControl(postgres_url)
    try:
        control.pause()
        run_id = add_run(users, owner, "queued",
                         created=datetime.now(timezone.utc) - timedelta(seconds=70))
        control.bind(run_id)
        unrelated = add_run(users, other, "queued",
                            created=datetime.now(timezone.utc) - timedelta(seconds=70))
        assert unrelated != run_id
        assert AlertService(users).evaluate()["worker_queue_stalled"] >= 1
        with users.sessions() as session:
            alert = session.scalar(select(AlertRecord).where(
                AlertRecord.run_id == run_id, AlertRecord.status == "open"))
            assert alert is not None
            alert_id = alert.id
        denied = Responder(users).respond(alert_id, owner)
        assert denied["action"]["decision"] == "action_denied"
        assert denied["action"]["reason"] == "demo_binding_mismatch"
        assert denied["action"]["result"] == "not_run"
        assert control.state()["status"] == "paused"
        assert Responder(users).respond(alert_id, owner) == denied
        with users.sessions() as session:
            assert session.scalar(select(func.count()).select_from(RunRecord).where(
                RunRecord.retry_of_run_id.is_not(None))) == 0
    finally:
        control.cleanup()
        control.close()
        users.close()
