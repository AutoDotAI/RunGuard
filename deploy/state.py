"""Validate and update the two successful on-host release records.

Only successful deployments rotate current/previous. Failed attempts append an
audit record without changing the known-good rollback target.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from release import release

ENVIRONMENTS = {"development", "production"}


def read_record(path: Path, environment: str) -> dict:
    record = json.loads(path.read_text())
    if not isinstance(record, dict) or record.get("environment") != environment:
        raise ValueError(f"invalid {path.name}: environment mismatch")
    if record.get("result") != "passed":
        raise ValueError(f"invalid {path.name}: not a successful release")
    release(*(record[key] for key in ("commit", "api", "web", "egress")))
    if type(record.get("run_id")) is not int or record["run_id"] <= 0:
        raise ValueError(f"invalid {path.name}: run id")
    for key in ("started_at", "finished_at"):
        if not isinstance(record.get(key), str) or not record[key]:
            raise ValueError(f"invalid {path.name}: {key}")
    return record


def write_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w") as stream:
        os.chmod(temp, 0o600)
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def append_attempt(state: Path, record: dict) -> None:
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "attempts.jsonl").open("a") as stream:
        os.chmod(state / "attempts.jsonl", 0o600)
        stream.write(json.dumps(record, sort_keys=True) + "\n")


def record_deploy(state: Path, record: dict) -> None:
    environment = record["environment"]
    current = state / "current.json"
    prior = read_record(current, environment) if current.exists() else None
    append_attempt(state, record)
    if record["result"] == "passed":
        if prior is not None:
            write_atomic(state / "previous.json", prior)
        write_atomic(current, record)
        write_atomic(state / "latest-success.json", record)
    else:
        write_atomic(state / "latest-failure.json", record)


def database_revision(path: Path) -> str:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as database:
        rows = database.execute("SELECT version_num FROM alembic_version").fetchall()
        if len(rows) != 1 or not isinstance(rows[0][0], str):
            raise ValueError("database does not have one Alembic revision")
        return rows[0][0]


def rollback_target(state: Path, environment: str, backup: Path, approval: Path) -> dict:
    current = read_record(state / "current.json", environment)
    previous = read_record(state / "previous.json", environment)
    if current == previous:
        raise ValueError("rollback target equals current release")
    proof = json.loads(approval.read_text())
    revision = database_revision(backup)
    if not isinstance(proof, dict) or proof.get("environment") != environment \
       or proof.get("target_commit") != previous["commit"] \
       or proof.get("database_revision") != revision \
       or proof.get("compatible") is not True:
        raise ValueError("previous image compatibility with current schema is unverified")
    return previous


def record_rollback(state: Path, environment: str, result: str) -> None:
    current = read_record(state / "current.json", environment)
    previous = read_record(state / "previous.json", environment)
    record = {**previous, "operation": "rollback", "result": result,
              "finished_at": datetime.now(timezone.utc).isoformat()}
    append_attempt(state, record)
    if result == "passed":
        write_atomic(state / "current.json", record)
        write_atomic(state / "previous.json", current)
        write_atomic(state / "latest-success.json", record)
    else:
        write_atomic(state / "latest-failure.json", record)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("deploy", "history", "preflight", "rollback-result"))
    parser.add_argument("--state", type=Path, default=Path("state"))
    parser.add_argument("--environment", required=True, choices=sorted(ENVIRONMENTS))
    parser.add_argument("--result", choices=("passed", "failed"))
    parser.add_argument("--commit")
    parser.add_argument("--api")
    parser.add_argument("--web")
    parser.add_argument("--egress")
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--started-at")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--approval", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "deploy":
            if not args.result or not args.run_id or args.run_id <= 0 or not args.started_at:
                raise ValueError("result, positive run id, and start time are required")
            data = release(args.commit, args.api, args.web, args.egress)
            record = {"environment": args.environment, **data, "run_id": args.run_id,
                      "started_at": args.started_at,
                      "finished_at": datetime.now(timezone.utc).isoformat(), "result": args.result}
            record_deploy(args.state, record)
        elif args.action == "history":
            current = read_record(args.state / "current.json", args.environment)
            previous = read_record(args.state / "previous.json", args.environment)
            if current == previous:
                raise ValueError("rollback target equals current release")
            print(json.dumps({"current": current, "previous": previous}))
        elif args.action == "preflight":
            if not args.backup or not args.approval:
                raise ValueError("backup and compatibility approval are required")
            print(json.dumps(rollback_target(args.state, args.environment, args.backup, args.approval)))
        else:
            if not args.result:
                raise ValueError("rollback result is required")
            record_rollback(args.state, args.environment, args.result)
    except (KeyError, OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"release state error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
