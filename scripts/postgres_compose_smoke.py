"""Check isolated local PostgreSQL health and named-volume persistence.

This creates and removes only a random Compose project. It does not start the
RunGuard application or touch the existing runguard SQLite volume.
"""

from __future__ import annotations

import os
from pathlib import Path
import json
import secrets
import socket
import subprocess
import time
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
PROJECT = f"rg-pg-smoke-{uuid4().hex[:12]}"
MARKER = uuid4().hex


def free_loopback_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


ENV = os.environ.copy()
ENV.update(
    RUNGUARD_AUTH_SECRET=secrets.token_hex(32),
    RUNGUARD_POSTGRES_PASSWORD=secrets.token_hex(32),
    RUNGUARD_POSTGRES_DB="runguard",
    RUNGUARD_POSTGRES_USER="runguard",
    RUNGUARD_POSTGRES_PORT=str(free_loopback_port()),
)
COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", str(ROOT / "compose.yaml")]


def compose(*args: str, capture: bool = False) -> str:
    result = subprocess.run(
        [*COMPOSE, *args],
        cwd=ROOT,
        env=ENV,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if capture else ""


def wait_healthy() -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        container_id = compose("ps", "-q", "postgres", capture=True)
        if container_id:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Health.Status}}", container_id],
                check=True,
                capture_output=True,
                text=True,
            )
            if result.stdout.strip() == "healthy":
                return
        time.sleep(2)
    raise RuntimeError("PostgreSQL did not become ready within 90 seconds")


def assert_loopback_port() -> None:
    with socket.create_connection(("127.0.0.1", int(ENV["RUNGUARD_POSTGRES_PORT"])), timeout=3):
        pass
    port = compose("port", "postgres", "5432", capture=True)
    assert port == f"127.0.0.1:{ENV['RUNGUARD_POSTGRES_PORT']}", port


def sql(query: str) -> str:
    return compose(
        "exec", "-T", "postgres", "psql", "-v", "ON_ERROR_STOP=1", "-U", "runguard", "-d", "runguard", "-Atqc", query, capture=True
    )


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
                file.write(
                    f"RUNGUARD_AUTH_SECRET={ENV['RUNGUARD_AUTH_SECRET']}\n"
                    f"RUNGUARD_POSTGRES_PASSWORD={ENV['RUNGUARD_POSTGRES_PASSWORD']}\n"
                )
            created_env = True
    try:
        compose("config", "--quiet")
        config = json.loads(compose("config", "--format", "json", capture=True))
        postgres = config["services"]["postgres"]
        assert set(postgres["networks"]) == {"database"}
        assert postgres["ports"][0]["host_ip"] == "127.0.0.1"
        assert config["volumes"]["postgres_data"]
        compose("up", "-d", "postgres")
        wait_healthy()
        assert_loopback_port()
        compose("ps", "postgres")
        sql("CREATE TABLE smoke_marker (value text NOT NULL)")
        sql(f"INSERT INTO smoke_marker (value) VALUES ('{MARKER}')")
        compose("restart", "postgres")
        wait_healthy()
        assert sql("SELECT value FROM smoke_marker") == MARKER
        compose("down")
        compose("up", "-d", "postgres")
        wait_healthy()
        assert sql("SELECT value FROM smoke_marker") == MARKER
        print("PASS: PostgreSQL healthy and test row retained after restart and recreate")
    finally:
        try:
            compose("down", "--volumes")
        finally:
            if created_env:
                env_path.unlink()


if __name__ == "__main__":
    main()
