"""Run a host CLI worker against the local Compose database without copying secrets."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess

from sqlalchemy.engine import URL


ROOT = Path(__file__).resolve().parents[2]


def compose_database_url(project: str) -> str:
    config_result = subprocess.run(
        ["docker", "compose", "-p", project, "config", "--format", "json"],
        cwd=ROOT, capture_output=True, text=True, check=True)
    service = json.loads(config_result.stdout)["services"]["postgres"]
    port = int(service["ports"][0]["published"])
    user = service["environment"]["POSTGRES_USER"]
    database = service["environment"]["POSTGRES_DB"]
    password_result = subprocess.run(
        ["docker", "compose", "-p", project, "exec", "-T", "--user", "999",
         "postgres", "cat", "/run/runguard-secrets/postgres_password"],
        cwd=ROOT, capture_output=True, text=True, check=True)
    password = password_result.stdout
    if not password:
        raise ValueError("Local PostgreSQL credential is empty")
    return URL.create("postgresql+psycopg", username=user, password=password,
                      host="127.0.0.1", port=port, database=database).render_as_string(
                          hide_password=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=("codex", "claude"))
    args = parser.parse_args()
    # Compose uses the checkout directory name when no project is supplied.
    project = os.environ.get("COMPOSE_PROJECT_NAME") or ROOT.name.lower()
    try:
        database_url = compose_database_url(project)
        subprocess.run(["docker", "compose", "-p", project, "stop", "worker"],
                       cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError):
        parser.exit(2, "Start the local Compose stack before the host worker.\n")
    environment = {**os.environ, "RUNGUARD_DATABASE_URL": database_url,
                   "RUNGUARD_MODEL_PROVIDER": args.provider}
    environment.pop("RUNGUARD_DATABASE_URL_FILE", None)
    os.chdir(ROOT / "backend")
    os.execvpe("uv", ["uv", "run", "python", "-m", "app.workers.runner"], environment)


if __name__ == "__main__":
    raise SystemExit(main())
