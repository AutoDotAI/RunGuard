"""Read-only schema check using the previous API image and a SQLite snapshot."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select

sys.path.insert(0, "/app")
from app.db import RunLogRecord, RunRecord, UserRecord, WorkerRecord


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--environment", choices=("development", "production"), required=True)
    parser.add_argument("--target-commit", required=True)
    args = parser.parse_args()
    try:
        with sqlite3.connect(f"file:{args.database}?mode=ro", uri=True) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise ValueError("snapshot integrity check failed")
            revisions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
            if len(revisions) != 1:
                raise ValueError("database does not have one Alembic revision")
            revision = revisions[0][0]
        head = ScriptDirectory.from_config(Config("/app/alembic.ini")).get_current_head()
        if revision != head:
            raise ValueError("previous image migration head differs from current database; operator review required")
        engine = create_engine(f"sqlite:///{args.database}")
        with engine.connect() as connection:
            for model in (UserRecord, WorkerRecord, RunRecord, RunLogRecord):
                connection.execute(select(model).limit(1)).fetchall()
        engine.dispose()
        print(json.dumps({"environment": args.environment, "target_commit": args.target_commit,
                          "database_revision": revision, "compatible": True}))
    except Exception as exc:
        print(f"schema compatibility failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
