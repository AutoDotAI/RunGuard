"""Owner-scoped alert rules and local operator delivery, without live services."""

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from pathlib import Path

import yaml
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.alerting import AlertLoop, AlertService, stamp
from app.db import RunRecord, UserRepository, WorkerRecord
from app.main import create_app
from app.operator_alerts import OperatorPublisher
from app.operator_inbox import sanitize_delivery


NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
SECRET = "test-only-signing-secret-32-bytes-minimum"


def add_run(users, owner, status, *, created=NOW, finished=None, error_code=None):
    worker_id, run_id = str(uuid4()), str(uuid4())
    with users.sessions() as session:
        session.add(WorkerRecord(id=worker_id, owner_user_id=owner,
                                 name="Example", kind="website_monitor", url="https://example.com/",
                                 config_json="{}", created_at=stamp(created),
                                 updated_at=stamp(created), archived_at=None))
        session.flush()
        session.add(RunRecord(id=run_id, worker_id=worker_id, owner_user_id=owner,
                              worker_name_snapshot="Example", kind="website_monitor",
                              url_snapshot="https://example.com/", config_snapshot_json="{}",
                              status=status, created_at=stamp(created),
                              started_at=stamp(created) if status in {"running", "failed", "succeeded"} else None,
                              finished_at=stamp(finished) if finished else None,
                              duration_ms=1000 if finished else None,
                              http_status=503 if error_code else None, result_json=None,
                              error_code=error_code, error_message="Target returned 503" if error_code else None,
                              retry_of_run_id=None, trace_id="a" * 32, traceparent=None))
        session.commit()
    return run_id


def repo(tmp_path):
    users = UserRepository(f"sqlite:///{tmp_path / 'alerts.db'}")
    users.migrate()
    alice = users.create("alice@example.test", "hash", stamp(NOW))["id"]
    bob = users.create("bob@example.test", "hash", stamp(NOW))["id"]
    return users, alice, bob


def test_alert_migration_preserves_preexisting_run_and_log(tmp_path):
    path = tmp_path / "before-alerts.db"
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["database_url"] = f"sqlite:///{path}"
    command.upgrade(config, "0003_active_run_index")
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES (?,?,?,?)",
                           ("owner", "owner@example.test", "hash", stamp(NOW)))
        connection.execute("INSERT INTO workers(id,owner_user_id,name,kind,url,config_json,created_at,updated_at) "
                           "VALUES (?,?,?,?,?,?,?,?)",
                           ("worker", "owner", "Example", "website_monitor", "https://example.com/", "{}", stamp(NOW), stamp(NOW)))
        connection.execute("INSERT INTO runs(id,worker_id,owner_user_id,worker_name_snapshot,kind,url_snapshot,"
                           "config_snapshot_json,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                           ("run", "worker", "owner", "Example", "website_monitor", "https://example.com/", "{}", "queued", stamp(NOW)))
        connection.execute("INSERT INTO run_logs(run_id,owner_user_id,timestamp,level,event,message) "
                           "VALUES (?,?,?,?,?,?)", ("run", "owner", stamp(NOW), "INFO", "RUN_QUEUED", "Run queued"))
        connection.commit()
    command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT status FROM runs WHERE id='run'").fetchone() == ("queued",)
        assert connection.execute("SELECT event FROM run_logs WHERE run_id='run'").fetchone() == ("RUN_QUEUED",)
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='alerts'").fetchone()


def test_two_distinct_terminal_failures_exact_window_boundary_and_resolution(tmp_path):
    users, alice, bob = repo(tmp_path)
    service = AlertService(users, clock=lambda: NOW)
    first = add_run(users, alice, "failed", created=NOW - timedelta(minutes=6),
                    finished=NOW - timedelta(minutes=5), error_code="HTTP_STATUS_ERROR")
    add_run(users, bob, "failed", finished=NOW, error_code="HTTP_STATUS_ERROR")
    add_run(users, alice, "running", created=NOW - timedelta(seconds=1))
    assert service.evaluate()["repeated_run_failure"] == 0
    latest = add_run(users, alice, "failed", finished=NOW, error_code="HTTP_STATUS_ERROR")
    assert service.evaluate()["repeated_run_failure"] == 1
    assert service.evaluate()["repeated_run_failure"] == 1
    alert = service.list_alerts(alice).items[0]
    assert alert.run_id == latest and alert.status == "open" and alert.severity == "warning"
    assert alert.trace_id == "a" * 32
    assert service.list_alerts(bob).total == 0
    # Exactly five minutes is included; one microsecond outside is excluded.
    service.evaluate(NOW + timedelta(microseconds=1))
    resolved = service.list_alerts(alice).items[0]
    assert resolved.status == "resolved" and resolved.resolved_at == stamp(NOW + timedelta(microseconds=1))
    service.evaluate(NOW + timedelta(microseconds=1))
    assert service.list_alerts(alice).total == 1
    users.close()


def test_stalled_queue_needs_unhealthy_heartbeat_and_resolves_on_claim(tmp_path):
    users, alice, _ = repo(tmp_path)
    heartbeat = tmp_path / "worker.lock"
    service = AlertService(users, clock=lambda: NOW, heartbeat_path=heartbeat)
    run_id = add_run(users, alice, "queued", created=NOW - timedelta(seconds=60))
    assert service.evaluate()["worker_queue_stalled"] == 0
    service.evaluate(NOW + timedelta(microseconds=1))
    assert service.list_alerts(alice).items[0].run_id == run_id
    assert service.list_alerts(alice).items[0].status == "open"
    with users.sessions() as session:
        run = session.get(RunRecord, run_id)
        run.status = "running"
        session.commit()
    service.evaluate(NOW + timedelta(seconds=1))
    assert service.list_alerts(alice).items[0].status == "resolved"
    # A healthy heartbeat must suppress a different delayed queued run.
    add_run(users, alice, "queued", created=NOW - timedelta(seconds=90))
    heartbeat.touch()
    os.utime(heartbeat, (NOW.timestamp(), NOW.timestamp()))
    assert service.evaluate()["worker_queue_stalled"] == 0
    users.close()


def test_successful_run_resolves_repeated_failure_and_survives_restart(tmp_path):
    users, alice, _ = repo(tmp_path)
    service = AlertService(users, clock=lambda: NOW)
    add_run(users, alice, "failed", finished=NOW - timedelta(seconds=3), error_code="HTTP_STATUS_ERROR")
    add_run(users, alice, "failed", finished=NOW - timedelta(seconds=2), error_code="HTTP_STATUS_ERROR")
    assert service.evaluate()["repeated_run_failure"] == 1
    add_run(users, alice, "succeeded", finished=NOW - timedelta(seconds=2))
    assert service.evaluate()["repeated_run_failure"] == 1  # A tie is not a later recovery.
    add_run(users, alice, "succeeded", finished=NOW - timedelta(seconds=1))
    assert service.evaluate()["repeated_run_failure"] == 0
    alert = service.list_alerts(alice).items[0]
    assert alert.status == "resolved" and alert.resolved_at == stamp(NOW)
    users.close()
    reopened = UserRepository(f"sqlite:///{tmp_path / 'alerts.db'}")
    assert AlertService(reopened, clock=lambda: NOW).list_alerts(alice).items[0].id == alert.id
    reopened.close()


def test_periodic_evaluation_opens_alert_with_idle_api_and_stopped_worker(tmp_path):
    users, alice, _ = repo(tmp_path)
    service = AlertService(users, clock=lambda: NOW)
    add_run(users, alice, "queued", created=NOW - timedelta(seconds=61))
    loop = AlertLoop(service, interval=0.02)
    loop.start()
    try:
        deadline = time.monotonic() + 1
        while service.list_alerts(alice).total == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert service.list_alerts(alice).items[0].kind == "worker_queue_stalled"
    finally:
        loop.stop()
        users.close()


def test_authenticated_api_returns_only_owned_alerts_and_run_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNGUARD_ALERT_INTERVAL_SECONDS", "0")
    with TestClient(create_app(secret=SECRET, database_url=f"sqlite:///{tmp_path / 'api.db'}")) as client:
        def register(email):
            result = client.post("/api/v1/auth/register", json={"email": email, "password": "StrongPassword123!"})
            assert result.status_code == 201
            return result.json()["user"]["id"], {"Authorization": f"Bearer {result.json()['access_token']}"}
        alice, alice_header = register("alice@example.test")
        bob, bob_header = register("bob@example.test")
        client.app.state.alerts.clock = lambda: NOW
        add_run(client.app.state.users, alice, "failed", finished=NOW - timedelta(seconds=1), error_code="HTTP_STATUS_ERROR")
        run_id = add_run(client.app.state.users, alice, "failed", finished=NOW, error_code="HTTP_STATUS_ERROR")
        assert client.get("/api/v1/alerts").status_code == 401
        owned = client.get("/api/v1/alerts", headers=alice_header)
        assert owned.status_code == 200 and owned.json()["total"] == 1
        assert owned.json()["items"][0]["run_id"] == run_id
        assert client.get("/api/v1/alerts", headers=bob_header).json() == {"items": [], "total": 0}
        assert client.get(f"/api/v1/runs/{run_id}", headers=bob_header).status_code == 404


def test_operator_payload_is_aggregate_and_receiver_rejects_identity_labels():
    sent = []
    publisher = OperatorPublisher("http://alertmanager:9093", transport=lambda url, body: sent.append((url, json.loads(body))),
                                  clock=lambda: NOW)
    publisher({"repeated_run_failure": 1, "worker_queue_stalled": 0})
    publisher({"repeated_run_failure": 20, "worker_queue_stalled": 0})
    publisher({"repeated_run_failure": 0, "worker_queue_stalled": 0})
    assert len(sent) == 3
    assert all(len(body) == 1 for _, body in sent)
    assert sent[0][1][0]["labels"] == sent[1][1][0]["labels"]
    assert sent[2][1][0]["endsAt"] == stamp(NOW)
    serialized = json.dumps(sent)
    for private in ("alice", "user_id", "email", "run_id", "https://", "token"):
        assert private not in serialized
    delivery = {"alerts": [{"status": "firing", "labels": sent[0][1][0]["labels"]}]}
    assert sanitize_delivery(delivery)[0]["status"] == "firing"
    delivery["alerts"][0]["labels"]["user_id"] = "secret"
    import pytest
    with pytest.raises(ValueError):
        sanitize_delivery(delivery)


def test_local_operator_wiring_and_alert_metric_have_bounded_labels():
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "compose.observability.yaml").read_text())
    manager = yaml.safe_load((root / "observability/alertmanager.yaml").read_text())
    assert compose["services"]["alertmanager"]["ports"] == ["127.0.0.1:9093:9093"]
    assert compose["services"]["operator-inbox"]["ports"] == ["127.0.0.1:9094:9094"]
    assert compose["services"]["api"]["environment"]["RUNGUARD_ALERTMANAGER_URL"] == "http://alertmanager:9093"
    assert manager["route"]["group_by"] == ["alertname", "kind"]
    assert manager["route"]["repeat_interval"] == "30m"
    assert manager["receivers"][0]["webhook_configs"][0]["send_resolved"] is True
    dashboard = json.loads((root / "observability/dashboards/runguard.json").read_text())
    assert any("runguard_alerts_open" in target["expr"]
               for panel in dashboard["panels"] for target in panel.get("targets", []))
