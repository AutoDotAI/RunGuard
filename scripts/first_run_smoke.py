#!/usr/bin/env python3
"""Isolated no-.env Compose startup, persistence, and legacy credential smoke."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def command(args: list[str], *, cwd: Path, env: dict[str, str], timeout: int = 240) -> str:
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                            text=True, timeout=timeout)
    if result.returncode:
        # Compose can echo interpolated credentials on failures. Do not include
        # its raw output in the smoke failure or CI logs.
        raise RuntimeError(f"Command failed ({result.returncode}): {args[0]} {args[1]}")
    return result.stdout


def request(url: str, payload: dict | None = None, token: str | None = None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(payload).encode() if payload is not None else None
    with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers,
                                                        method="POST" if body is not None else "GET"),
                                        timeout=15) as response:
        return response.status, json.load(response)


def wait_ready(api: str, web: str) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            if (urllib.request.urlopen(f"{api}/health", timeout=3).status == 200
                    and urllib.request.urlopen(f"{web}/signup", timeout=3).status == 200):
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(2)
    raise RuntimeError("Disposable API and web did not become healthy")


def volume_secrets(project: str, env: dict[str, str], cwd: Path) -> dict[str, str]:
    program = ("import json,pathlib; p=pathlib.Path('/s'); "
               "print(json.dumps({n:(p/n).read_text() for n in "
               "('auth_secret','postgres_password','database_url')}))")
    output = command(["docker", "run", "--rm", "-v",
                      f"{project}_runguard_local_secrets:/s:ro", "--entrypoint", "python",
                      "python:3.12-slim", "-c", program], cwd=cwd, env=env)
    return json.loads(output)


def write_legacy_env(path: Path, auth: str, password: str) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(f"RUNGUARD_AUTH_SECRET={auth}\nRUNGUARD_POSTGRES_PASSWORD={password}\n")


def main() -> None:
    project = "rg-first-" + secrets.token_hex(5)
    other = "rg-first-" + secrets.token_hex(5)
    assert project != other and project.startswith("rg-first-")
    with tempfile.TemporaryDirectory(prefix="runguard-first-") as temporary:
        checkout = Path(temporary)
        for name in ("backend", "frontend"):
            shutil.copytree(ROOT / name, checkout / name,
                            ignore=shutil.ignore_patterns(".venv", "node_modules", "__pycache__",
                                                          ".pytest_cache", "dist", ".output",
                                                          "*.db", "*.db-*", "private-backups"))
        shutil.copy2(ROOT / "compose.yaml", checkout / "compose.yaml")
        base_env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("RUNGUARD_", "COMPOSE_"))}
        env = {**base_env, "RUNGUARD_POSTGRES_PORT": str(free_port()),
               "RUNGUARD_API_PORT": str(free_port()), "RUNGUARD_WEB_PORT": str(free_port())}
        compose = ["docker", "compose", "-p", project]

        def dc(*args: str, timeout: int = 240) -> str:
            return command([*compose, *args], cwd=checkout, env=env, timeout=timeout)

        api = f"http://127.0.0.1:{env['RUNGUARD_API_PORT']}"
        web = f"http://127.0.0.1:{env['RUNGUARD_WEB_PORT']}"
        try:
            assert not (checkout / ".env").exists()
            dc("config", "--quiet")
            dc("up", "--build", "-d", timeout=600)
            wait_ready(api, web)
            first = volume_secrets(project, env, checkout)
            assert len(first["auth_secret"]) >= 64 and len(first["postgres_password"]) >= 64
            # A different clean project must not reuse either credential.
            command(["docker", "compose", "-p", other, "run", "--rm", "--no-deps",
                     "secrets-init"], cwd=checkout, env=env)
            second = volume_secrets(other, env, checkout)
            assert first["auth_secret"] != second["auth_secret"]
            assert first["postgres_password"] != second["postgres_password"]
            email = f"bootstrap-{secrets.token_hex(5)}@example.test"
            password = secrets.token_urlsafe(24)
            status, account = request(f"{api}/api/v1/auth/register",
                                      {"email": email, "password": password})
            assert status == 201
            token = account["access_token"]
            status, login = request(f"{api}/api/v1/auth/login",
                                    {"email": email, "password": password})
            assert status == 200 and login["access_token"]
            status, worker = request(f"{api}/api/v1/workers", {"name": "First run monitor",
                                    "kind": "website_monitor", "url": "https://example.com"}, token)
            assert status == 201
            status, queued = request(f"{api}/api/v1/workers/{worker['id']}/runs", {}, token)
            assert status == 202
            run_id = queued["run_id"]
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                _, item = request(f"{api}/api/v1/runs/{run_id}", token=token)
                if item["status"] in ("succeeded", "failed"):
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Website Monitor run did not finish")
            _, logs = request(f"{api}/api/v1/runs/{run_id}/logs", token=token)
            assert logs["items"]
            _, providers = request(f"{api}/api/v1/ai/providers", token=token)
            assert all(not item["available"] for item in providers["providers"]
                       if item["provider"] in ("codex", "claude", "litellm"))
            public_payloads = json.dumps([account, login, worker, queued, item, logs, providers])
            assert all(secret not in public_payloads for secret in first.values())
            container_logs = dc("logs", "--no-color")
            assert all(secret not in container_logs for secret in first.values())

            def verify_history() -> None:
                status, renewed = request(f"{api}/api/v1/auth/login",
                                          {"email": email, "password": password})
                assert status == 200
                renewed_token = renewed["access_token"]
                assert request(f"{api}/api/v1/runs/{run_id}", token=renewed_token)[0] == 200
                assert request(f"{api}/api/v1/runs/{run_id}/logs", token=renewed_token)[1]["items"]

            dc("down")
            dc("up", "-d")
            wait_ready(api, web)
            assert volume_secrets(project, env, checkout) == first
            verify_history()

            # Simulate upgrade of a populated PostgreSQL volume with the old
            # .env but without the new secret volume. This touches only the
            # unique disposable project.
            dc("down")
            write_legacy_env(checkout / ".env", first["auth_secret"], first["postgres_password"])
            command(["docker", "volume", "rm", f"{project}_runguard_local_secrets"],
                    cwd=checkout, env=env)
            dc("up", "-d")
            wait_ready(api, web)
            assert volume_secrets(project, env, checkout) == first
            verify_history()
            dc("down")
            write_legacy_env(checkout / ".env", first["auth_secret"], "wrong-password")
            try:
                dc("up", "-d")
            except RuntimeError:
                pass
            else:
                raise AssertionError("A changed PostgreSQL password unexpectedly started the stack")
            dc("down")
            write_legacy_env(checkout / ".env", first["auth_secret"], first["postgres_password"])
            dc("up", "-d")
            wait_ready(api, web)
            verify_history()
            print("PASS first-run Compose: no .env, unique private secrets, monitor, restart, "
                  "legacy upgrade, wrong-credential denial")
        finally:
            for name in (project, other):
                if not name.startswith("rg-first-"):
                    continue
                subprocess.run(["docker", "compose", "-p", name, "down", "--volumes",
                                "--remove-orphans"], cwd=checkout, env=env,
                               capture_output=True, text=True, timeout=90)


if __name__ == "__main__":
    main()
