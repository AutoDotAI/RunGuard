"""Fresh and existing-data local credential bootstrapping."""

import os
import stat

import pytest

from app.local_bootstrap import ensure_local_secrets
from app.auth import TokenService
from app.db import configured_database_url


def prepare(tmp_path, **kwargs):
    directory = tmp_path / "secrets"
    pgdata = tmp_path / "pgdata"
    pgdata.mkdir(parents=True)
    ensure_local_secrets(directory, pgdata, app_uid=os.getuid(),
                         postgres_uid=os.getuid(), **kwargs)
    return directory, pgdata


def test_fresh_secrets_unique_private_and_stable(tmp_path):
    first, pgdata = prepare(tmp_path / "first")
    second, _ = prepare(tmp_path / "second")
    initial = {name: (first / name).read_text()
               for name in ("auth_secret", "postgres_password", "database_url")}
    assert initial["auth_secret"] != (second / "auth_secret").read_text()
    assert initial["postgres_password"] != (second / "postgres_password").read_text()
    assert len(initial["auth_secret"]) >= 64
    for name in initial:
        assert stat.S_IMODE((first / name).stat().st_mode) == 0o600
    ensure_local_secrets(first, pgdata, app_uid=os.getuid(), postgres_uid=os.getuid())
    assert initial == {name: (first / name).read_text() for name in initial}


def test_existing_data_requires_and_preserves_old_credentials(tmp_path):
    directory = tmp_path / "secrets"
    pgdata = tmp_path / "pgdata"
    pgdata.mkdir()
    (pgdata / "PG_VERSION").write_text("16")
    with pytest.raises(ValueError, match="requires the original"):
        ensure_local_secrets(directory, pgdata, app_uid=os.getuid(), postgres_uid=os.getuid())
    assert not list(directory.iterdir())
    auth, password = "a" * 64, "p" * 64
    ensure_local_secrets(directory, pgdata, auth_secret=auth, postgres_password=password,
                         app_uid=os.getuid(), postgres_uid=os.getuid())
    assert (directory / "auth_secret").read_text() == auth
    assert (directory / "postgres_password").read_text() == password
    with pytest.raises(ValueError, match="differs"):
        ensure_local_secrets(directory, pgdata, auth_secret="b" * 64,
                             postgres_password=password, app_uid=os.getuid(),
                             postgres_uid=os.getuid())
    assert (directory / "auth_secret").read_text() == auth


def test_incomplete_or_weak_configuration_fails_closed(tmp_path):
    directory, pgdata = prepare(tmp_path / "base")
    (directory / "database_url").unlink()
    with pytest.raises(ValueError, match="incomplete"):
        ensure_local_secrets(directory, pgdata, app_uid=os.getuid(), postgres_uid=os.getuid())
    fresh = tmp_path / "weak"
    with pytest.raises(ValueError, match="at least 32 bytes"):
        prepare(fresh, auth_secret="short")


def test_runtime_reads_files_without_environment_secrets(tmp_path, monkeypatch):
    directory, _ = prepare(tmp_path)
    monkeypatch.delenv("RUNGUARD_AUTH_SECRET", raising=False)
    monkeypatch.delenv("RUNGUARD_DATABASE_URL", raising=False)
    monkeypatch.setenv("RUNGUARD_AUTH_SECRET_FILE", str(directory / "auth_secret"))
    monkeypatch.setenv("RUNGUARD_DATABASE_URL_FILE", str(directory / "database_url"))
    assert TokenService().secret == (directory / "auth_secret").read_text()
    assert configured_database_url() == (directory / "database_url").read_text()
