"""Bounded, offline tests for the isolated queued-worker alert drill."""

from datetime import datetime, timedelta, timezone

import pytest
from app.alerting import AlertService
from app.db import RunRecord, UserRepository
from app.demo_worker import DemoControl, DemoDenied, DemoWorkerRunner
from app.models import WorkerCreate
from app.store import PersistentStore, Problem


NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def demo(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNGUARD_DEMO_MODE", "true")
    monkeypatch.setenv("RUNGUARD_DEMO_PROJECT", "rg-demo-test")
    monkeypatch.setenv("RUNGUARD_COMPOSE_PROJECT", "rg-demo-test")
    monkeypatch.delenv("RUNGUARD_PUBLIC_MODE", raising=False)
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    users = UserRepository(database_url)
    users.migrate()
    alice = users.create("alice@example.test", "hash", NOW.isoformat())["id"]
    bob = users.create("bob@example.test", "hash", NOW.isoformat())["id"]
    yield users, PersistentStore(users), alice, bob
    users.close()


def queued_fixture(store, owner):
    worker = store.create_worker(owner, WorkerCreate(name="Local demo fixture",
                                                      kind="website_monitor",
                                                      url="https://example.com/demo"))
    return store.run_worker(owner, worker.id).run_id


@pytest.mark.parametrize("mode,project,actual,public", [
    (None, "rg-demo-test", "rg-demo-test", None),
    ("false", "rg-demo-test", "rg-demo-test", None),
    ("true", "ordinary", "ordinary", None),
    ("true", "rg-demo-test", "production", None),
    ("true", "rg-demo-test", "rg-demo-test", "1"),
])
def test_control_denied_without_explicit_isolated_mode(tmp_path, monkeypatch, mode, project, actual, public):
    for key, value in (("RUNGUARD_DEMO_MODE", mode), ("RUNGUARD_DEMO_PROJECT", project),
                       ("RUNGUARD_COMPOSE_PROJECT", actual), ("RUNGUARD_PUBLIC_MODE", public)):
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    with pytest.raises(DemoDenied):
        DemoControl(f"sqlite:///{tmp_path / 'absent.db'}")
    assert not list(tmp_path.glob("*.demo-state.json"))


def test_pause_refuses_unrelated_active_jobs_and_is_idempotent(demo):
    users, store, alice, _ = demo
    unrelated = queued_fixture(store, alice)
    control = DemoControl(users.database_url)
    try:
        with pytest.raises(DemoDenied, match="Unrelated"):
            control.pause()
        assert control.state() is None
        with DemoWorkerRunner(users.database_url) as runner:
            assert runner.run_once() is False  # no arm, even though a job exists
        with users.sessions() as session:
            session.get(RunRecord, unrelated).status = "failed"
            session.commit()
        assert control.pause() == control.pause()
        assert control.state()["status"] == "paused"
        assert control.cleanup() is None
        assert control.cleanup() is None
    finally:
        control.close()


def test_one_fixture_stalls_at_strict_boundary_then_recovers_without_network(demo):
    users, store, alice, bob = demo
    control = DemoControl(users.database_url)
    try:
        control.pause()
        run_id = queued_fixture(store, alice)
        assert control.bind(run_id) == control.bind(run_id)
        service = AlertService(users, clock=lambda: NOW,
                               heartbeat_path=control.state_path.with_name("absent-heartbeat"))
        with users.sessions() as session:
            run = session.get(RunRecord, run_id)
            run.created_at = (NOW - timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
            session.commit()
        assert service.evaluate()["worker_queue_stalled"] == 0
        assert service.evaluate(NOW + timedelta(microseconds=1))["worker_queue_stalled"] == 1
        first = service.list_alerts(alice).items[0]
        assert first.run_id == run_id and first.status == "open"
        assert service.list_alerts(bob).total == 0
        assert service.evaluate(NOW + timedelta(seconds=1))["worker_queue_stalled"] == 1
        assert service.list_alerts(alice).total == 1
        assert control.resume() == control.resume()
        with DemoWorkerRunner(users.database_url) as runner:
            assert runner.run_once()
        assert store.get_run(alice, run_id).status == "succeeded"
        with pytest.raises(Problem) as error:
            store.get_run(bob, run_id)
        assert error.value.status == 404
        events = [row.event for row in store.get_logs(alice, run_id, 0).items]
        assert events[:3] == ["RUN_QUEUED", "DEMO_WORKER_PAUSED", "DEMO_WORKER_RESUMED"]
        assert service.evaluate(NOW + timedelta(seconds=2))["worker_queue_stalled"] == 0
        alert = service.list_alerts(alice).items[0]
        assert alert.id == first.id and alert.status == "resolved"
        assert control.cleanup() is None
    finally:
        control.close()


def test_bind_and_resume_refuse_other_runs_and_cleanup_releases_pause(demo):
    users, store, alice, bob = demo
    control = DemoControl(users.database_url)
    try:
        control.pause()
        first = queued_fixture(store, alice)
        second = queued_fixture(store, bob)
        with pytest.raises(DemoDenied, match="single queued"):
            control.bind(first)
        with users.sessions() as session:
            session.get(RunRecord, second).status = "failed"
            session.commit()
        control.bind(first)
        with pytest.raises(DemoDenied, match="different fixture"):
            control.bind(second)
        with users.sessions() as session:
            session.get(RunRecord, second).status = "queued"
            session.commit()
        with pytest.raises(DemoDenied, match="Unrelated"):
            control.resume()
        with users.sessions() as session:
            session.get(RunRecord, second).status = "failed"
            session.commit()
        assert control.cleanup()["status"] == "resumed"
        with DemoWorkerRunner(users.database_url) as runner:
            assert runner.run_once()
        assert control.cleanup() is None
    finally:
        control.close()


def test_demo_runner_never_claims_unbound_job(demo):
    users, store, alice, bob = demo
    control = DemoControl(users.database_url)
    try:
        control.pause()
        fixture = queued_fixture(store, alice)
        control.bind(fixture)
        control.resume()
        unrelated = queued_fixture(store, bob)
        with DemoWorkerRunner(users.database_url) as runner:
            assert runner.run_once() is False
        assert store.get_run(alice, fixture).status == "queued"
        assert store.get_run(bob, unrelated).status == "queued"
    finally:
        control.close()
