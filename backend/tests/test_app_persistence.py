"""Durability and schema checks for worker, run, and log storage."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import RunLogRecord, UserRepository
from app.main import create_app


SECRET = "test-only-signing-secret-32-bytes-minimum"


def app_for(path: Path):
    return create_app(secret=SECRET, database_url=f"sqlite:///{path}")


def auth(token: str):
    return {"Authorization": f"Bearer {token}"}


def register(client, email: str):
    response = client.post("/api/v1/auth/register", json={
        "email": email, "password": "CorrectHorse123!"})
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


def test_documented_alembic_console_command_imports_app_from_backend(tmp_path):
    """A console entry point must work without pytest's pythonpath setting."""
    backend = Path(__file__).resolve().parents[1]
    database = tmp_path / "cli.db"
    environment = os.environ.copy()
    environment["RUNGUARD_DATABASE_URL"] = f"sqlite:///{database}"
    result = subprocess.run(
        [str(Path(sys.executable).with_name("alembic")), "upgrade", "head"],
        cwd=backend, env=environment, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0007_worker_heartbeat"


def test_upgrade_from_users_preserves_account_and_adds_required_schema(tmp_path):
    path = tmp_path / "nested" / "app.db"
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["database_url"] = f"sqlite:///{path}"
    command.upgrade(config, "0001_users")
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO users VALUES (?, ?, ?, ?)", (
            "user-one", "owner@example.com", "hashed-password", "2026-01-01T00:00:00Z"))
    command.upgrade(config, "head")

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT email FROM users WHERE id='user-one'").fetchone()[0] == "owner@example.com"
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0007_worker_heartbeat"
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"users", "workers", "runs", "run_logs", "alerts"} <= tables
        for table, expected in {
            "workers": {"id", "owner_user_id", "config_json", "archived_at", "created_at", "updated_at"},
            "runs": {"id", "worker_id", "owner_user_id", "url_snapshot", "config_snapshot_json",
                     "result_json", "error_code", "error_message", "created_at", "started_at", "finished_at"},
            "run_logs": {"id", "run_id", "owner_user_id", "timestamp", "event", "message"},
        }.items():
            assert expected <= {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        assert {row[2] for row in connection.execute("PRAGMA foreign_key_list(workers)")} == {"users"}
        assert {row[2] for row in connection.execute("PRAGMA foreign_key_list(runs)")} == {"users", "workers", "runs"}
        assert {row[2] for row in connection.execute("PRAGMA foreign_key_list(run_logs)")} == {"users", "runs"}
        assert {row[1] for row in connection.execute("PRAGMA index_list(workers)")} >= {"ix_workers_owner_archived"}
        assert {row[1] for row in connection.execute("PRAGMA index_list(runs)")} >= {
            "ix_runs_owner_created", "ix_runs_worker_created", "ix_runs_worker_status"}
        assert {row[1] for row in connection.execute("PRAGMA index_list(run_logs)")} >= {
            "ix_run_logs_run_id", "ix_run_logs_owner_id"}
        assert connection.execute("SELECT type FROM pragma_table_info('run_logs') WHERE name='id'").fetchone()[0] == "INTEGER"

    repo = UserRepository(f"sqlite:///{path}")
    try:
        with repo.engine.connect() as connection:
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        with pytest.raises(IntegrityError), repo.sessions() as session:
            session.add(RunLogRecord(run_id="nonexistent", owner_user_id="user-one",
                                     timestamp="2026-01-01T00:00:00Z", level="INFO",
                                     event="TEST", message="orphan"))
            session.commit()
    finally:
        repo.close()


def test_restart_snapshots_archive_cursor_and_owner_isolation(tmp_path):
    path = tmp_path / "app.db"
    with TestClient(app_for(path)) as client:
        alice = register(client, "alice@example.com")
        bob = register(client, "bob@example.com")
        worker = client.post("/api/v1/workers", headers=auth(alice), json={
            "name": "Original", "kind": "website_monitor", "url": "https://example.com/"})
        assert worker.status_code == 201
        worker_id = worker.json()["id"]
        queued = client.post(f"/api/v1/workers/{worker_id}/runs", headers=auth(alice))
        assert queued.status_code == 202
        run_id = queued.json()["run_id"]
        first_log = client.get(f"/api/v1/runs/{run_id}/logs", headers=auth(alice)).json()["items"][0]
        assert first_log["event"] == "RUN_QUEUED"

    with TestClient(app_for(path)) as client:
        assert client.get("/api/v1/workers", headers=auth(alice)).json()["total"] == 1
        assert client.get("/api/v1/runs", headers=auth(alice)).json()["total"] == 1
        assert client.get(f"/api/v1/runs/{run_id}", headers=auth(alice)).json()["status"] == "queued"
        assert client.get(f"/api/v1/runs/{run_id}/logs", headers=auth(alice)).json()["items"] == [first_log]
        assert client.get("/api/v1/workers", headers=auth(bob)).json()["items"] == []
        assert client.get("/api/v1/runs", headers=auth(bob)).json()["items"] == []
        assert client.get("/api/v1/dashboard/summary", headers=auth(bob)).json()["recent_runs"] == []
        for method, path_suffix, body in [
            ("get", f"/workers/{worker_id}", None),
            ("patch", f"/workers/{worker_id}", {"name": "Stolen"}),
            ("delete", f"/workers/{worker_id}", None),
            ("post", f"/workers/{worker_id}/runs", None),
            ("get", f"/runs/{run_id}", None),
            ("get", f"/runs/{run_id}/logs", None),
            ("post", f"/runs/{run_id}/retry", None),
        ]:
            response = getattr(client, method)("/api/v1" + path_suffix, headers=auth(bob), json=body) if body else getattr(client, method)("/api/v1" + path_suffix, headers=auth(bob))
            assert response.status_code == 404, (method, path_suffix, response.text)
        assert client.get("/api/v1/runs", headers=auth(bob), params={"worker_id": worker_id}).status_code == 404
        assert client.patch(f"/api/v1/workers/{worker_id}", headers=auth(alice), json={
            "name": "Updated", "url": "https://example.org/"}).status_code == 200
        assert client.delete(f"/api/v1/workers/{worker_id}", headers=auth(alice)).status_code == 204
        with client.app.state.users.sessions() as session:
            session.add(RunLogRecord(run_id=run_id, owner_user_id=client.app.state.store.user_by_email("alice@example.com")["id"],
                                     timestamp="2026-01-01T00:00:01Z", level="INFO",
                                     event="TEST_CURSOR", message="Later entry"))
            session.commit()

    with TestClient(app_for(path)) as client:
        assert client.get("/api/v1/workers", headers=auth(alice)).json()["items"] == []
        assert client.get(f"/api/v1/workers/{worker_id}", headers=auth(alice)).status_code == 404
        run = client.get(f"/api/v1/runs/{run_id}", headers=auth(alice)).json()
        assert run["url"] == "https://example.com/"
        assert run["worker_name"] == "Original"
        logs = client.get(f"/api/v1/runs/{run_id}/logs", headers=auth(alice)).json()
        assert [row["id"] for row in logs["items"]] == sorted(row["id"] for row in logs["items"])
        assert [row["event"] for row in logs["items"]] == ["RUN_QUEUED", "TEST_CURSOR"]
        later = client.get(f"/api/v1/runs/{run_id}/logs", headers=auth(alice), params={
            "after_id": first_log["id"]}).json()
        assert [row["event"] for row in later["items"]] == ["TEST_CURSOR"]
        assert client.get(f"/api/v1/runs/{run_id}/logs", headers=auth(alice), params={
            "after_id": later["next_after_id"]}).json()["items"] == []
