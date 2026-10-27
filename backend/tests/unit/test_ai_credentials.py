"""Encrypted API credentials stay account-owned and survive local restarts."""

import os
import stat

import pytest
from sqlalchemy import event, select

from app.ai_credentials import CredentialRepository, CredentialUnavailable, ensure_model_key
from app.db import AiCredentialRecord, UserRepository
from app.local_bootstrap import ensure_local_secrets


def setup(tmp_path):
    users = UserRepository(f"sqlite:///{tmp_path / 'app.db'}")
    users.migrate()
    owner_a = users.create("a@example.com", "hash", "2026-01-01T00:00:00Z")["id"]
    owner_b = users.create("b@example.com", "hash", "2026-01-01T00:00:00Z")["id"]
    secrets_dir = tmp_path / "secrets"
    pgdata = tmp_path / "pgdata"
    pgdata.mkdir()
    ensure_local_secrets(secrets_dir, pgdata, app_uid=os.getuid(), postgres_uid=os.getuid())
    model_dir = secrets_dir / "model"
    ensure_model_key(users, model_dir)
    return users, owner_a, owner_b, model_dir


def test_private_unique_and_stable_install_key(tmp_path):
    users, _, _, model_dir = setup(tmp_path / "first")
    other_users, _, _, other_dir = setup(tmp_path / "second")
    try:
        key_file = model_dir / "model_encryption_key"
        original = key_file.read_bytes()
        assert original != (other_dir / "model_encryption_key").read_bytes()
        assert original != (model_dir.parent / "auth_secret").read_text().encode()
        assert original != (model_dir.parent / "postgres_password").read_text().encode()
        assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(model_dir.stat().st_mode) == 0o700
        ensure_model_key(users, model_dir)
        assert key_file.read_bytes() == original
    finally:
        users.close()
        other_users.close()


def test_ciphertext_roundtrip_replacement_deletion_and_owner_isolation(tmp_path):
    users, owner_a, owner_b, model_dir = setup(tmp_path)
    key_file = model_dir / "model_encryption_key"
    repo = CredentialRepository(users, key_file)
    first = "sk-test-first-secret-123"
    second = "sk-test-second-secret-456"
    statements = []

    def capture(_connection, _cursor, statement, parameters, _context, _many):
        statements.append((statement, repr(parameters)))

    event.listen(users.engine, "before_cursor_execute", capture)
    try:
        repo.put(owner_a, "openai", first)
        with users.sessions() as session:
            stored = session.get(AiCredentialRecord, (owner_a, "openai"))
            assert stored.ciphertext != first
            assert first not in stored.ciphertext
            assert stored.ciphertext.startswith("gAAAA")
        assert repo.get(owner_a, "openai") == first
        assert repo.get(owner_b, "openai") is None
        assert repo.configured(owner_a) == {"openai": True, "anthropic": False}
        assert repo.configured(owner_b) == {"openai": False, "anthropic": False}
        repo.put(owner_a, "openai", second)
        assert repo.get(owner_a, "openai") == second
        assert all(first not in repr(item) and second not in repr(item) for item in statements)
        repo.delete(owner_b, "openai")
        assert repo.get(owner_a, "openai") == second
        repo.delete(owner_a, "openai")
        assert repo.get(owner_a, "openai") is None
        with users.sessions() as session:
            assert session.scalars(select(AiCredentialRecord)).all() == []
    finally:
        event.remove(users.engine, "before_cursor_execute", capture)
        users.close()


def test_restart_preserves_existing_data_and_missing_key_fails_closed(tmp_path):
    users, owner_a, _, model_dir = setup(tmp_path)
    key_file = model_dir / "model_encryption_key"
    repo = CredentialRepository(users, key_file)
    repo.put(owner_a, "anthropic", "sk-ant-test-secret-123")
    users.close()

    reopened = UserRepository(f"sqlite:///{tmp_path / 'app.db'}")
    reopened.migrate()
    try:
        ensure_model_key(reopened, model_dir)
        assert CredentialRepository(reopened, key_file).get(owner_a, "anthropic") == "sk-ant-test-secret-123"
        key_file.unlink()
        with pytest.raises(CredentialUnavailable, match="Provider credential is unavailable") as exc:
            CredentialRepository(reopened, key_file).get(owner_a, "anthropic")
        assert "sk-ant" not in str(exc.value)
        with pytest.raises(CredentialUnavailable):
            CredentialRepository(reopened, key_file).delete(owner_a, "anthropic")
        with reopened.sessions() as session:
            assert session.get(AiCredentialRecord, (owner_a, "anthropic")) is not None
        with pytest.raises(CredentialUnavailable):
            ensure_model_key(reopened, model_dir)
        assert not key_file.exists()
    finally:
        reopened.close()


def test_wrong_key_and_invalid_input_fail_without_exposing_key(tmp_path):
    users, owner_a, _, model_dir = setup(tmp_path)
    key_file = model_dir / "model_encryption_key"
    repo = CredentialRepository(users, key_file)
    repo.put(owner_a, "openai", "sk-test-live-secret-123")
    wrong_users, _, _, wrong_dir = setup(tmp_path / "other")
    wrong_key = wrong_dir / "model_encryption_key"
    try:
        with users.sessions() as session:
            original_ciphertext = session.get(AiCredentialRecord, (owner_a, "openai")).ciphertext
        with pytest.raises(CredentialUnavailable) as exc:
            CredentialRepository(users, wrong_key).get(owner_a, "openai")
        assert "sk-test-live" not in str(exc.value)
        with pytest.raises(CredentialUnavailable):
            CredentialRepository(users, wrong_key).put(owner_a, "openai", "sk-test-replacement-456")
        with pytest.raises(CredentialUnavailable):
            CredentialRepository(users, wrong_key).delete(owner_a, "openai")
        with users.sessions() as session:
            assert session.get(AiCredentialRecord, (owner_a, "openai")).ciphertext == original_ciphertext
        assert repo.get(owner_a, "openai") == "sk-test-live-secret-123"
        key_file.write_bytes(wrong_key.read_bytes())
        with pytest.raises(CredentialUnavailable):
            ensure_model_key(users, model_dir)
        with pytest.raises(ValueError, match="Unsupported provider"):
            repo.put(owner_a, "other", "sk-test-live-secret-123")
        with pytest.raises(ValueError, match="Invalid provider credential length") as exc:
            repo.put(owner_a, "openai", "sk-test-live-secret-123\n")
        assert "sk-test-live" not in str(exc.value)
    finally:
        users.close()
        wrong_users.close()


def test_upgrade_generates_first_key_only_without_ciphertext(tmp_path):
    users = UserRepository(f"sqlite:///{tmp_path / 'legacy.db'}")
    users.migrate()
    secrets_dir = tmp_path / "secrets"
    pgdata = tmp_path / "pgdata"
    pgdata.mkdir()
    ensure_local_secrets(secrets_dir, pgdata, app_uid=os.getuid(), postgres_uid=os.getuid())
    model_dir = secrets_dir / "model"
    assert not (model_dir / "model_encryption_key").exists()
    ensure_model_key(users, model_dir)
    assert (model_dir / "model_encryption_key").exists()
    users.close()
