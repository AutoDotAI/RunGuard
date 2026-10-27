"""Private, persistent credentials for a local Compose project.

Compose resolves variables before containers start, so the init service writes
password files to a named volume. It never prints their contents.
"""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path
from urllib.parse import quote


FILES = ("auth_secret", "postgres_password", "database_url")


def _read_private(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Invalid local credential file: {path.name}")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError(f"Local credential file must have mode 0600: {path.name}")
    return path.read_text()


def _write_private(path: Path, value: str, uid: int) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w") as file:
            file.write(value)
            file.flush()
            os.fsync(file.fileno())
        os.chown(path, uid, uid)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def ensure_local_secrets(directory: Path, pgdata: Path, *, auth_secret: str = "",
                         postgres_password: str = "", user: str = "runguard",
                         database: str = "runguard", app_uid: int = 10001,
                         postgres_uid: int = 999) -> None:
    if directory.is_symlink():
        raise ValueError("Local credential directory cannot be a symlink")
    directory.mkdir(mode=0o711, parents=True, exist_ok=True)
    os.chmod(directory, 0o711)
    paths = {name: directory / name for name in FILES}
    present = {name for name, path in paths.items() if path.exists() or path.is_symlink()}
    if present and present != set(FILES):
        raise ValueError("Local credentials are incomplete; refusing to regenerate them")
    if present:
        saved_auth = _read_private(paths["auth_secret"])
        saved_password = _read_private(paths["postgres_password"])
        saved_url = _read_private(paths["database_url"])
        if auth_secret and auth_secret != saved_auth:
            raise ValueError("Configured authentication secret differs from saved local credential")
        if postgres_password and postgres_password != saved_password:
            raise ValueError("Configured PostgreSQL password differs from saved local credential")
        expected_url = _database_url(user, database, saved_password)
        if saved_url != expected_url:
            raise ValueError("Configured PostgreSQL user/database differs from saved local credential")
        return
    if (pgdata / "PG_VERSION").exists() and (not auth_secret or not postgres_password):
        raise ValueError("Existing PostgreSQL data requires the original .env credentials")
    auth_secret = auth_secret or secrets.token_hex(32)
    postgres_password = postgres_password or secrets.token_hex(32)
    if len(auth_secret.encode()) < 32 or not postgres_password:
        raise ValueError("Local authentication secret must be at least 32 bytes and PostgreSQL password nonempty")
    _write_private(paths["postgres_password"], postgres_password, postgres_uid)
    _write_private(paths["auth_secret"], auth_secret, app_uid)
    _write_private(paths["database_url"], _database_url(user, database, postgres_password), app_uid)


def _database_url(user: str, database: str, password: str) -> str:
    return (f"postgresql+psycopg://{quote(user, safe='')}:{quote(password, safe='')}"
            f"@postgres:5432/{quote(database, safe='')}")


def main() -> int:
    ensure_local_secrets(
        Path(os.environ.get("RUNGUARD_LOCAL_SECRETS_DIR", "/run/runguard-secrets")),
        Path(os.environ.get("RUNGUARD_EXISTING_PG_DATA_DIR", "/pgdata")),
        auth_secret=os.environ.get("RUNGUARD_AUTH_SECRET", ""),
        postgres_password=os.environ.get("RUNGUARD_POSTGRES_PASSWORD", ""),
        user=os.environ.get("RUNGUARD_POSTGRES_USER", "runguard"),
        database=os.environ.get("RUNGUARD_POSTGRES_DB", "runguard"),
    )
    print("Local credentials ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
