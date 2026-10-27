"""Probe local PostgreSQL routes without starting the RunGuard website.

The random Compose project and its volume are disposable. The normal project's
SQLite and PostgreSQL volumes are never addressed by this script.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
PROJECT = f"rg-pg-network-{uuid4().hex[:12]}"


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def run(command: list[str], *, env: dict[str, str], capture: bool = False,
        cwd: Path = ROOT) -> str:
    result = subprocess.run(command, cwd=cwd, env=env, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ""


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="runguard-network-") as temporary:
        private_env = Path(temporary) / "worker.env"
        password = secrets.token_hex(32)
        secret = secrets.token_hex(32)
        private_env.write_text("RUNGUARD_MODEL_PROVIDER=disabled\n")
        private_env.chmod(0o600)
        env = os.environ.copy()
        env.update(RUNGUARD_POSTGRES_DB="runguard", RUNGUARD_POSTGRES_USER="runguard",
                   RUNGUARD_POSTGRES_PASSWORD=password, RUNGUARD_AUTH_SECRET=secret,
                   RUNGUARD_POSTGRES_PORT=str(free_port()),
                   RUNGUARD_MODEL_ENV_FILE=str(private_env))
        # Compose env_file is a host file read at container creation, not a mount.
        # The opt-in path supplies it without writing a project .env.
        compose = ["docker", "compose", "-p", PROJECT, "-f", str(ROOT / "compose.yaml")]

        def dc(*args: str, capture: bool = False) -> str:
            return run([*compose, *args], env=env, capture=capture)

        try:
            config = json.loads(dc("config", "--format", "json", capture=True))
            for name in ("api", "worker", "migrate"):
                service = config["services"][name]
                assert service["environment"]["RUNGUARD_DATABASE_URL_FILE"] == (
                    "/run/runguard-secrets/database_url")
                assert "database" in service["networks"]
                assert all("runguard_local_secrets" in volume["source"]
                           for volume in service.get("volumes", []))
            assert config["services"]["postgres"]["ports"][0]["host_ip"] == "127.0.0.1"
            dc("up", "--build", "-d", "postgres")
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                container = dc("ps", "-q", "postgres", capture=True)
                if container and run(["docker", "inspect", "--format", "{{.State.Health.Status}}",
                                      container], env=env, capture=True) == "healthy":
                    break
                time.sleep(2)
            else:
                raise RuntimeError("Disposable PostgreSQL did not become healthy")

            query = ("from app.db import UserRepository; from sqlalchemy import text; "
                     "r=UserRepository(); c=r.engine.connect(); "
                     "assert c.execute(text('SELECT 1')).scalar_one()==1; c.close()")
            # One-off processes use the API/worker images and network but do not
            # start either long-running service or the website.
            dc("build", "api")
            for service in ("api", "worker"):
                dc("run", "--rm", "--no-deps", service, "python", "-c", query)

            dc("run", "--rm", "--no-deps", "migrate")

            host_url = (f"postgresql+psycopg://runguard:{password}@127.0.0.1:"
                        f"{env['RUNGUARD_POSTGRES_PORT']}/runguard")
            host_env = {**env, "RUNGUARD_DATABASE_URL": host_url}
            host_worker_probe = (
                "from app.workers.runner import WorkerRunner\n"
                "with WorkerRunner(capability_reporter=lambda: {'providers': []}) as worker:\n"
                "    assert worker.db.engine.url.host == '127.0.0.1'\n"
                "    assert worker.claim_oldest() is None\n"
            )
            run(["uv", "run", "--locked", "python", "-c", host_worker_probe],
                env=host_env, capture=True, cwd=ROOT / "backend")
            print("PASS: API and worker image reach PostgreSQL; host worker uses loopback only")
        finally:
            dc("down", "--volumes")


if __name__ == "__main__":
    main()
