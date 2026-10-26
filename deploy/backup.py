"""Create and verify a private, consistent SQLite backup, including WAL commits."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def backup(source: Path, destination_dir: Path, owner_uid: int | None = None,
           owner_gid: int | None = None) -> Path:
    if not source.is_file():
        raise ValueError("source database does not exist")
    destination_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = "runguard-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".db"
    destination = destination_dir / name
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as live:
            with sqlite3.connect(destination) as snapshot:
                os.chmod(destination, 0o600)
                live.backup(snapshot)
                result = snapshot.execute("PRAGMA integrity_check").fetchone()
                if result != ("ok",):
                    raise ValueError("backup failed SQLite integrity check")
        if owner_uid is not None and owner_gid is not None:
            os.chown(destination, owner_uid, owner_gid)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination-dir", required=True, type=Path)
    parser.add_argument("--owner-uid", type=int)
    parser.add_argument("--owner-gid", type=int)
    args = parser.parse_args()
    try:
        print(backup(args.source, args.destination_dir, args.owner_uid, args.owner_gid))
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"backup error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
