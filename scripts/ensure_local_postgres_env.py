"""Add the PostgreSQL password required by Compose to an existing local .env.

This keeps the SQLite-era auth secret unchanged and never prints either secret.
"""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path


def ensure_postgres_password(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{path} must be an existing regular file; keep the original auth secret")
    original = path.read_text()
    lines = original.splitlines(keepends=True)
    auth = [line.partition("=")[2].strip() for line in lines
            if line.startswith("RUNGUARD_AUTH_SECRET=")]
    if len(auth) != 1 or len(auth[0].encode()) < 32:
        raise ValueError(f"{path} must contain one existing RUNGUARD_AUTH_SECRET of at least 32 bytes")
    password_lines = [index for index, line in enumerate(lines)
                      if line.startswith("RUNGUARD_POSTGRES_PASSWORD=")]
    if len(password_lines) > 1:
        raise ValueError(f"{path} contains duplicate RUNGUARD_POSTGRES_PASSWORD entries")
    if password_lines and lines[password_lines[0]].partition("=")[2].strip():
        os.chmod(path, 0o600)
        return False

    new_line = f"RUNGUARD_POSTGRES_PASSWORD={secrets.token_hex(32)}\n"
    if password_lines:
        lines[password_lines[0]] = new_line
    else:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(new_line)
    temporary = path.with_name(f".{path.name}.postgres-prep-{secrets.token_hex(6)}")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w") as file:
            file.writelines(lines)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    try:
        added = ensure_postgres_password(args.env_file)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"local PostgreSQL preparation: {exc}\n")
    print("Local PostgreSQL password added; existing auth secret preserved."
          if added else "Local PostgreSQL password already configured; auth secret preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
