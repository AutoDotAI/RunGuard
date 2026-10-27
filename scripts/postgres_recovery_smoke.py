"""Exercise local pg_dump/pg_restore without touching the normal RunGuard project."""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import RunLogRecord, RunRecord  # noqa: E402
from app.main import create_app  # noqa: E402

PROJECT = f"rg-recovery-smoke-{uuid4().hex[:10]}"
SECRET = secrets.token_hex(32)
PASSWORD = secrets.token_hex(32)
USER = "runguard"
DATABASE = "runguard"
RESTORED = "runguard_restored"
EMAIL = "backup-smoke@example.com"
LOGIN_PASSWORD = "CorrectHorse123!"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


PORT = free_port()
ENV = os.environ.copy()
ENV.update(
    COMPOSE_PROJECT_NAME=PROJECT,
    RUNGUARD_AUTH_SECRET=SECRET,
    RUNGUARD_POSTGRES_PASSWORD=PASSWORD,
    RUNGUARD_POSTGRES_USER=USER,
    RUNGUARD_POSTGRES_DB=DATABASE,
    RUNGUARD_POSTGRES_PORT=str(PORT),
    RUNGUARD_ALERT_INTERVAL_SECONDS="0",
)
COMPOSE = ["docker", "compose", "-f", str(ROOT / "compose.yaml")]


def command(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, env=ENV, check=check, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def compose(*args: str) -> str:
    return command(*COMPOSE, *args).stdout.strip()


def wait_healthy() -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        result = command(*COMPOSE, "ps", "-q", "postgres")
        if result.stdout.strip():
            health = command("docker", "inspect", "--format",
                             "{{.State.Health.Status}}", result.stdout.strip()).stdout.strip()
            if health == "healthy":
                return
        time.sleep(2)
    raise RuntimeError("disposable PostgreSQL did not become healthy")


def database_url(name: str) -> str:
    return f"postgresql+psycopg://{USER}:{PASSWORD}@127.0.0.1:{PORT}/{name}"


def main() -> None:
    env_path = ROOT / ".env"
    created_env = False
    if not env_path.exists():
        try:
            fd = os.open(env_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w") as file:
                file.write(f"RUNGUARD_AUTH_SECRET={SECRET}\nRUNGUARD_POSTGRES_PASSWORD={PASSWORD}\n")
            created_env = True
    try:
        compose("up", "-d", "postgres")
        wait_healthy()
        with TestClient(create_app(secret=SECRET, database_url=database_url(DATABASE))) as client:
            registered = client.post("/api/v1/auth/register", json={
                "email": EMAIL, "password": LOGIN_PASSWORD})
            assert registered.status_code == 201, registered.text
            bearer = {"Authorization": f"Bearer {registered.json()['access_token']}"}
            created = client.post("/api/v1/workers", headers=bearer, json={
                "name": "Backup sample", "kind": "website_monitor",
                "url": "https://example.com/"})
            assert created.status_code == 201, created.text
            run_id = str(uuid4())
            with client.app.state.users.sessions() as session:
                session.add(RunRecord(
                    id=run_id, worker_id=created.json()["id"],
                    owner_user_id=registered.json()["user"]["id"],
                    worker_name_snapshot="Backup sample", kind="website_monitor",
                    url_snapshot="https://example.com/", config_snapshot_json="{}",
                    status="failed", created_at="2026-01-01T00:00:00Z",
                    finished_at="2026-01-01T00:00:01Z", duration_ms=1000,
                    error_code="HTTP_ERROR", error_message="Test fixture"))
                session.flush()
                session.add(RunLogRecord(
                    run_id=run_id, owner_user_id=registered.json()["user"]["id"],
                    timestamp="2026-01-01T00:00:01Z", level="ERROR",
                    event="HTTP_ERROR", message="Test fixture"))
                session.commit()

        with tempfile.TemporaryDirectory(prefix="runguard-recovery-") as directory:
            archive = Path(directory) / "runguard.dump"
            recovery = str(ROOT / "scripts" / "local_pg_recovery.sh")
            command(recovery, "backup", str(archive))
            assert archive.is_file() and archive.stat().st_size > 0
            assert archive.stat().st_mode & 0o077 == 0
            assert command(recovery, "backup", str(archive), check=False).returncode != 0
            assert command(recovery, "restore", str(archive), DATABASE, check=False).returncode != 0
            corrupt = Path(directory) / "corrupt.dump"
            corrupt.write_bytes(b"not a PostgreSQL archive")
            assert command(recovery, "restore", str(corrupt), "corrupt_restore", check=False).returncode != 0
            command(recovery, "restore", str(archive), RESTORED)
            assert command(recovery, "restore", str(archive), RESTORED, check=False).returncode != 0

        with TestClient(create_app(secret=SECRET, database_url=database_url(RESTORED))) as client:
            login = client.post("/api/v1/auth/login", json={
                "email": EMAIL, "password": LOGIN_PASSWORD})
            assert login.status_code == 200, login.text
            bearer = {"Authorization": f"Bearer {login.json()['access_token']}"}
            run = client.get(f"/api/v1/runs/{run_id}", headers=bearer)
            assert run.status_code == 200, run.text
            assert run.json()["status"] == "failed"
            logs = client.get(f"/api/v1/runs/{run_id}/logs", headers=bearer)
            assert logs.status_code == 200, logs.text
            assert [item["event"] for item in logs.json()["items"]] == ["HTTP_ERROR"]
        print("PASS: private pg_dump archive restored to a fresh database; login/run/log reads work")
    finally:
        try:
            compose("down", "--volumes")
        finally:
            if created_env:
                env_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
