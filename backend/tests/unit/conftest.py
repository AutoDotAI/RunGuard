"""Small, offline rule fixtures; no API server or external service is started."""

import pytest

from app.db import UserRepository
from app.store import PersistentStore


@pytest.fixture
def store(tmp_path):
    users = UserRepository(f"sqlite:///{tmp_path / 'unit.db'}")
    users.migrate()
    try:
        yield PersistentStore(users)
    finally:
        users.close()


@pytest.fixture
def owners(store):
    alice = store.create_user("alice@example.com", "hash-a")["id"]
    bob = store.create_user("bob@example.com", "hash-b")["id"]
    return alice, bob
