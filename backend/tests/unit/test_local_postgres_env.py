"""The cutover prerequisite must preserve the existing local auth secret."""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ensure_local_postgres_env.py"
AUTH_SECRET = "existing-auth-signing-secret-more-than-32-bytes"


def run_prepare(env_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--env-file", str(env_file)],
        capture_output=True, text=True, check=False,
    )


def test_adds_stable_password_without_changing_auth_secret(tmp_path):
    env_file = tmp_path / ".env"
    original = f"# keep this comment\nRUNGUARD_AUTH_SECRET={AUTH_SECRET}\n"
    env_file.write_text(original)
    env_file.chmod(0o644)
    first = run_prepare(env_file)
    assert first.returncode == 0, first.stderr
    after_first = env_file.read_text()
    assert after_first.startswith(original)
    password = after_first.split("RUNGUARD_POSTGRES_PASSWORD=", 1)[1].strip()
    assert len(password) == 64 and all(character in "0123456789abcdef" for character in password)
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert AUTH_SECRET not in first.stdout + first.stderr
    assert password not in first.stdout + first.stderr

    second = run_prepare(env_file)
    assert second.returncode == 0, second.stderr
    assert env_file.read_text() == after_first
    assert "already configured" in second.stdout


def test_replaces_only_empty_password(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(f"RUNGUARD_AUTH_SECRET={AUTH_SECRET}\nRUNGUARD_POSTGRES_PASSWORD=\nKEEP=unchanged\n")
    assert run_prepare(env_file).returncode == 0
    updated = env_file.read_text()
    assert f"RUNGUARD_AUTH_SECRET={AUTH_SECRET}\n" in updated
    assert "KEEP=unchanged\n" in updated
    assert updated.count("RUNGUARD_POSTGRES_PASSWORD=") == 1
    assert "RUNGUARD_POSTGRES_PASSWORD=\n" not in updated


def test_refuses_missing_auth_and_duplicate_password(tmp_path):
    missing = tmp_path / "missing.env"
    assert run_prepare(missing).returncode == 2
    env_file = tmp_path / ".env"
    env_file.write_text("RUNGUARD_AUTH_SECRET=short\n")
    before = env_file.read_text()
    assert run_prepare(env_file).returncode == 2
    assert env_file.read_text() == before
    env_file.write_text(
        f"RUNGUARD_AUTH_SECRET={AUTH_SECRET}\n"
        "RUNGUARD_POSTGRES_PASSWORD=first\nRUNGUARD_POSTGRES_PASSWORD=second\n"
    )
    before = env_file.read_text()
    assert run_prepare(env_file).returncode == 2
    assert env_file.read_text() == before
