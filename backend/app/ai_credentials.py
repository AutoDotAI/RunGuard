"""Account-owned provider keys encrypted with a private local installation key."""

from __future__ import annotations

import os
import stat
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select

from .db import AiCredentialRecord, UserRepository


PROVIDERS = ("openai", "anthropic")
DEFAULT_KEY_FILE = Path("/run/runguard-secrets/model/model_encryption_key")


class CredentialUnavailable(RuntimeError):
    """A provider credential cannot be safely opened. Never include key material."""

    def __init__(self):
        super().__init__("Provider credential is unavailable")


def _key_file(path: Path) -> Fernet:
    try:
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise CredentialUnavailable()
        return Fernet(path.read_bytes())
    except (OSError, ValueError, TypeError):
        raise CredentialUnavailable() from None


def ensure_model_key(users: UserRepository, directory: Path) -> None:
    """Create a key only when no stored ciphertext can depend on an old one.

    The migration service calls this after Alembic and PostgreSQL are ready.
    Existing installations with no credential rows can safely get their first
    key; losing a key after rows exist always stops startup instead.
    """
    if directory.is_symlink() or not directory.is_dir():
        raise CredentialUnavailable()
    if stat.S_IMODE(directory.stat().st_mode) != 0o700:
        raise CredentialUnavailable()
    path = directory / "model_encryption_key"
    with users.sessions() as session:
        has_ciphertext = bool(session.scalar(select(func.count()).select_from(AiCredentialRecord)))
    if path.exists() or path.is_symlink():
        cipher = _key_file(path)
        if has_ciphertext:
            with users.sessions() as session:
                rows = session.scalars(select(AiCredentialRecord.ciphertext)).all()
            try:
                for ciphertext in rows:
                    cipher.decrypt(ciphertext.encode("ascii"))
            except (InvalidToken, UnicodeError, ValueError, TypeError):
                raise CredentialUnavailable() from None
        return
    if has_ciphertext:
        raise CredentialUnavailable()
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as file:
            file.write(Fernet.generate_key())
            file.flush()
            os.fsync(file.fileno())
    except OSError:
        raise CredentialUnavailable() from None


class CredentialRepository:
    def __init__(self, users: UserRepository, key_file: Path | None = None):
        self.users = users
        self.key_file = key_file or Path(os.environ.get("RUNGUARD_MODEL_ENCRYPTION_KEY_FILE", DEFAULT_KEY_FILE))

    def _cipher(self) -> Fernet:
        return _key_file(self.key_file)

    @staticmethod
    def _provider(provider: str) -> str:
        if provider not in PROVIDERS:
            raise ValueError("Unsupported provider")
        return provider

    def put(self, owner: str, provider: str, api_key: str) -> None:
        provider = self._provider(provider)
        if (not isinstance(api_key, str) or not 8 <= len(api_key) <= 512
                or api_key != api_key.strip() or any(ord(char) < 32 for char in api_key)):
            raise ValueError("Invalid provider credential length")
        ciphertext = self._cipher().encrypt(api_key.encode()).decode("ascii")
        with self.users.sessions.begin() as session:
            row = session.get(AiCredentialRecord, (owner, provider))
            if row is None:
                session.add(AiCredentialRecord(owner_user_id=owner, provider=provider,
                                               ciphertext=ciphertext, updated_at=datetime.now(timezone.utc).isoformat()))
            else:
                row.ciphertext = ciphertext
                row.updated_at = datetime.now(timezone.utc).isoformat()

    def get(self, owner: str, provider: str) -> str | None:
        provider = self._provider(provider)
        with self.users.sessions() as session:
            row = session.get(AiCredentialRecord, (owner, provider))
            if row is None:
                return None
            ciphertext = row.ciphertext
        try:
            return self._cipher().decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError, TypeError):
            raise CredentialUnavailable() from None

    def delete(self, owner: str, provider: str) -> None:
        provider = self._provider(provider)
        with self.users.sessions.begin() as session:
            row = session.get(AiCredentialRecord, (owner, provider))
            if row is not None:
                session.delete(row)

    def is_configured(self, owner: str, provider: str) -> bool:
        return self.get(owner, provider) is not None

    def configured(self, owner: str) -> dict[str, bool]:
        return {provider: self.is_configured(owner, provider) for provider in PROVIDERS}
