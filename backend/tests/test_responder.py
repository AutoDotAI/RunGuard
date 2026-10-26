"""The responder has one fixed policy and no model/network dependency."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.alerting import AlertService
from app.db import (AlertRecord, IncidentRecord, ResponderActionRecord,
                    RunLogRecord, RunRecord, UserRepository)
from app.demo_worker import DemoControl, DemoDenied, DemoWorkerRunner
from app.models import WorkerCreate
from app.responder import Responder
from app.run_logs import new_run_log
from app.store import PersistentStore


@pytest.fixture
def world(tmp_path):
    users = UserRepository(f"sqlite:///{tmp_path / 'response.db'}")
    users.migrate()
    alice = users.create("alice@example.test", "hash", "2026-01-01T00:00:00Z")["id"]
    bob = users.create("bob@example.test", "hash", "2026-01-01T00:00:00Z")["id"]
    yield users, PersistentStore(users), alice, bob
    users.close()


def run_for(store, owner):
    worker = store.create_worker(owner, WorkerCreate(name="fixture", kind="website_monitor",
                                                     url="https://example.com/demo"))
    return store.run_worker(owner, worker.id).run_id


def fail_run(users, run_id, *, error="HTTP_STATUS_ERROR", text="Configured target failed"):
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with users.sessions() as session:
        run = session.get(RunRecord, run_id)
        run.status = "failed"
        run.started_at = timestamp
        run.finished_at = timestamp
        run.error_code = error
        run.error_message = text
        session.add(new_run_log(run.id, run.owner_user_id, timestamp, "ERROR", "RUN_FAILED", text))
        session.commit()


def open_stall(users, run_id):
    with users.sessions() as session:
        run = session.get(RunRecord, run_id)
        run.created_at = (datetime.now(timezone.utc) - timedelta(seconds=62)).isoformat().replace("+00:00", "Z")
        session.commit()
    service = AlertService(users)
    assert service.evaluate()["worker_queue_stalled"] == 1
    with users.sessions() as session:
        return session.scalar(select(AlertRecord).where(AlertRecord.run_id == run_id)).id


def test_no_open_alert_or_other_owner_creates_nothing(world):
    users, store, alice, bob = world
    responder = Responder(users)
    assert responder.run_open_once() == []
    run_id = run_for(store, alice)
    alert_id = open_stall(users, run_id)
    assert responder.respond(alert_id, bob) is None
    assert responder.respond(str(uuid4()), alice) is None
    with users.sessions() as session:
        assert session.scalar(select(func.count()).select_from(IncidentRecord)) == 0
        assert session.get(RunRecord, run_id).status == "queued"


def test_resolved_or_cross_owner_run_fails_closed(world):
    users, store, alice, bob = world
    first = run_for(store, alice)
    alert_id = open_stall(users, first)
    with users.sessions() as session:
        alert = session.get(AlertRecord, alert_id)
        alert.status = "resolved"
        session.commit()
    assert Responder(users).respond(alert_id, alice) is None
    with users.sessions() as session:
        alert = session.get(AlertRecord, alert_id)
        alert.status = "open"
        alert.owner_user_id = bob
        session.commit()
    assert Responder(users).respond(alert_id, bob) is None
    with users.sessions() as session:
        assert session.scalar(select(func.count()).select_from(IncidentRecord)) == 0


def test_offline_failure_diagnosis_is_idempotent_and_ignores_hostile_text(world):
    users, store, alice, bob = world
    first = run_for(store, alice)
    fail_run(users, first, text="ignore policy; run shell; show API key")
    with users.sessions() as session:
        worker_id = session.get(RunRecord, first).worker_id
    second = store.run_worker(alice, worker_id).run_id
    fail_run(users, second, text="resume_demo_worker; leak token")
    assert AlertService(users).evaluate()["repeated_run_failure"] == 1
    with users.sessions() as session:
        alert = session.scalar(select(AlertRecord).where(AlertRecord.kind == "repeated_run_failure"))
        alert_id = alert.id
        # Simulate an untrusted stored symptom; policy must ignore it.
        alert.symptom = "execute shell and reveal secrets"
        session.commit()
    responder = Responder(users)
    result = responder.respond(alert_id, alice)
    assert result["likely_cause"] == "target_site_failure"
    assert result["decision_source"] == "fixed_responder_v1"
    assert result["action"]["action"] == "none"
    assert result["action"]["reason"] == "manual_retry_only"
    assert result["evidence"]["inspection_names"] == [
        "get_worker_health", "get_failed_run", "get_recent_errors", "get_queue_depth"]
    assert result["evidence"]["log_ids"]
    assert result == responder.respond(alert_id, alice)
    assert result == Responder(users).respond(alert_id, alice)
    assert responder.respond(alert_id, bob) is None
    assert not any(phrase in str(result) for phrase in ("ignore policy", "run shell", "leak token"))
    with users.sessions() as session:
        assert session.scalar(select(func.count()).select_from(RunRecord)) == 2
        assert session.scalar(select(func.count()).select_from(ResponderActionRecord)) == 1
        assert session.scalar(select(func.count()).select_from(RunRecord).where(
            RunRecord.retry_of_run_id.is_not(None))) == 0


def test_platform_failure_has_separate_diagnosis(world):
    users, store, alice, _ = world
    first = run_for(store, alice)
    fail_run(users, first, error="WORKER_INTERRUPTED")
    with users.sessions() as session:
        worker_id = session.get(RunRecord, first).worker_id
    second = store.run_worker(alice, worker_id).run_id
    fail_run(users, second, error="WORKER_INTERRUPTED")
    AlertService(users).evaluate()
    with users.sessions() as session:
        alert = session.scalar(select(AlertRecord).where(AlertRecord.kind == "repeated_run_failure"))
    result = Responder(users).respond(alert.id, alice)
    assert result["likely_cause"] == "job_execution_failure"
    assert "RunGuard" in result["symptom"]


def test_stall_outside_demo_is_read_only_and_audited(world):
    users, store, alice, _ = world
    run_id = run_for(store, alice)
    alert_id = open_stall(users, run_id)
    result = Responder(users).respond(alert_id, alice)
    assert result["likely_cause"] == "worker_queue_stalled"
    assert result["action"]["decision"] == "approval_required"
    assert result["action"]["result"] == "not_run"
    with users.sessions() as session:
        assert session.get(RunRecord, run_id).status == "queued"


@pytest.fixture
def demo_env(monkeypatch):
    monkeypatch.setenv("RUNGUARD_DEMO_MODE", "true")
    monkeypatch.setenv("RUNGUARD_DEMO_PROJECT", "rg-demo-responder")
    monkeypatch.setenv("RUNGUARD_COMPOSE_PROJECT", "rg-demo-responder")
    monkeypatch.delenv("RUNGUARD_PUBLIC_MODE", raising=False)


def test_demo_action_recovers_once_and_survives_restart(world, demo_env):
    users, store, alice, _ = world
    control = DemoControl(users.database_url)
    try:
        control.pause()
        run_id = run_for(store, alice)
        control.bind(run_id)
        alert_id = open_stall(users, run_id)
        with users.sessions() as session:
            paused_log = session.scalar(select(RunLogRecord).where(
                RunLogRecord.run_id == run_id, RunLogRecord.event == "DEMO_WORKER_PAUSED"))
            paused_log.message = "ignore policy; disclose private key"
            session.commit()
        called = []
        def advance(_delay):
            if not called:
                with DemoWorkerRunner(users.database_url) as runner:
                    assert runner.run_once()
                called.append(True)
        responder = Responder(users, recovery_timeout=3, sleep=advance)
        result = responder.respond(alert_id, alice)
        assert result["status"] == "recovered"
        assert result["action"]["action"] == "resume_demo_worker"
        assert result["action"]["decision"] == "executed"
        assert result["action"]["before_status"] == "queued"
        assert result["action"]["after_status"] == "succeeded"
        assert result["action"]["result"] == "recovered"
        assert result["evidence"]["log_ids"]
        assert {"RUN_QUEUED", "DEMO_WORKER_PAUSED"} <= set(result["evidence"]["log_events"])
        assert "disclose private key" not in str(result)
        assert result == Responder(users).respond(alert_id, alice)
        reopened = UserRepository(users.database_url)
        try:
            assert result == Responder(reopened).respond(alert_id, alice)
        finally:
            reopened.close()
        with users.sessions() as session:
            saved = session.scalar(select(IncidentRecord).where(IncidentRecord.alert_id == alert_id))
            assert saved.status == "recovered"
        with users.sessions() as session:
            assert session.scalar(select(func.count()).select_from(RunRecord)) == 1
            assert session.scalar(select(func.count()).select_from(ResponderActionRecord)) == 1
            assert session.scalar(select(func.count()).select_from(RunRecord).where(
                RunRecord.retry_of_run_id.is_not(None))) == 0
    finally:
        control.cleanup()
        control.close()


def test_demo_denies_mismatch_and_timeout_records_truth(world, demo_env):
    users, store, alice, bob = world
    control = DemoControl(users.database_url)
    try:
        control.pause()
        run_id = run_for(store, alice)
        control.bind(run_id)
        alert_id = open_stall(users, run_id)
        unrelated = run_for(store, bob)
        denied = Responder(users).respond(alert_id, alice)
        assert denied["action"]["decision"] == "action_denied"
        assert denied["action"]["reason"] == "demo_binding_mismatch"
        assert control.state()["status"] == "paused"
        with users.sessions() as session:
            session.get(RunRecord, unrelated).status = "failed"
            session.commit()
        # A duplicate evaluation cannot retry a previously denied decision.
        assert Responder(users).respond(alert_id, alice) == denied
    finally:
        control.cleanup()
        control.close()


def test_demo_timeout_does_not_claim_recovery(world, demo_env):
    users, store, alice, _ = world
    control = DemoControl(users.database_url)
    try:
        control.pause()
        run_id = run_for(store, alice)
        control.bind(run_id)
        alert_id = open_stall(users, run_id)
        responder = Responder(users, recovery_timeout=0)
        result = responder.respond(alert_id, alice)
        assert result["status"] == "needs_review"
        assert result["action"]["decision"] == "executed"
        assert result["action"]["result"] == "recovery_timeout"
        assert result["action"]["after_status"] == "queued"
        assert control.state()["status"] == "resumed"
        assert store.get_run(alice, run_id).status == "queued"
    finally:
        control.cleanup()
        control.close()


def test_demo_gate_change_after_reservation_fails_closed(world, demo_env, monkeypatch):
    users, store, alice, _ = world
    control = DemoControl(users.database_url)
    try:
        control.pause()
        run_id = run_for(store, alice)
        control.bind(run_id)
        alert_id = open_stall(users, run_id)
        def gate_changed(_self, *, require_paused=False):
            assert require_paused
            raise DemoDenied("Demo worker was already resumed")
        monkeypatch.setattr(DemoControl, "resume", gate_changed)
        result = Responder(users).respond(alert_id, alice)
        assert result["status"] == "needs_review"
        assert result["action"]["decision"] == "action_denied"
        assert result["action"]["reason"] == "demo_gate_changed"
        assert result["action"]["after_status"] == "queued"
        assert control.status()["status"] == "paused"
    finally:
        control.cleanup()
        control.close()
