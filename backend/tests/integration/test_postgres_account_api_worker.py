"""Account-owned API keys cross the real PostgreSQL queue without entering runs.

RUNGUARD_TEST_POSTGRES_URL must point to a disposable local PostgreSQL database.
Each test creates and drops only its own schema.
"""

import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.ai_credentials import CredentialRepository, ensure_model_key
from app.db import RunRecord, UserRepository
from app.models import WorkerCreate
from app.outbound import FetchResponse
from app.store import PersistentStore
from app.workers.runner import WorkerRunner


PAGE = FetchResponse(200, {"content-type": "text/html"},
                     b"<article>Useful factual content about the subject. " * 5 + b"</article>")
SUMMARY = "# Article\n- Fact one.\n- Fact two.\n- Fact three."


@pytest.fixture
def postgres_url():
    source = os.environ.get("RUNGUARD_TEST_POSTGRES_URL")
    if not source:
        pytest.skip("RUNGUARD_TEST_POSTGRES_URL is not configured")
    url = make_url(source)
    if url.get_backend_name() != "postgresql":
        pytest.fail("RUNGUARD_TEST_POSTGRES_URL must use PostgreSQL")
    schema = f"runguard_account_api_{uuid4().hex}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated = url.update_query_dict({"options": f"-csearch_path={schema}"})
    try:
        yield isolated.render_as_string(hide_password=False)
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.mark.parametrize("provider,model", [
    ("openai", "openai/gpt-4.1-mini"),
    ("anthropic", "anthropic/claude-sonnet-4-6"),
])
def test_api_key_owner_isolation_revocation_and_snapshot(
        postgres_url, tmp_path, monkeypatch, provider, model):
    from app import outbound
    from app.workers import runner as runner_module
    from app.workers.llm import LiteLLMClient

    # Keep the normal site's worker lock untouched while using an isolated
    # test schema in the same local PostgreSQL service.
    monkeypatch.setattr(runner_module, "POSTGRES_WORKER_LOCK_KEY", uuid4().int % (2**63 - 1))
    users = UserRepository(postgres_url)
    users.migrate()
    model_dir = tmp_path / "model"
    model_dir.mkdir(mode=0o700)
    ensure_model_key(users, model_dir)
    monkeypatch.setenv("RUNGUARD_MODEL_ENCRYPTION_KEY_FILE",
                       str(model_dir / "model_encryption_key"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("RUNGUARD_MODEL_PROVIDER", "disabled")
    monkeypatch.setattr(outbound, "fetch", lambda _url: PAGE)
    seen = []
    monkeypatch.setattr(LiteLLMClient, "complete", lambda _self, **kwargs:
                        seen.append(kwargs) or SUMMARY)
    try:
        store = PersistentStore(users)
        alice = store.create_user("alice@example.com", "hash")["id"]
        bob = store.create_user("bob@example.com", "hash")["id"]
        keys = CredentialRepository(users, model_dir / "model_encryption_key")
        keys.put(alice, provider, "sk-test-alice-secret")
        keys.put(bob, provider, "sk-test-bob-secret")

        class Settings:
            def get(self, _owner):
                return SimpleNamespace(provider_path="api", provider="litellm",
                                       model=model, available=True)

        store.ai_settings = Settings()
        alice_worker = store.create_worker(alice, WorkerCreate(
            name="Alice summary", kind="url_summarizer", url="https://example.com/a"))
        bob_worker = store.create_worker(bob, WorkerCreate(
            name="Bob summary", kind="url_summarizer", url="https://example.com/b"))
        alice_run = store.run_worker(alice, alice_worker.id)
        bob_run = store.run_worker(bob, bob_worker.id)
        with users.sessions() as session:
            rows = [session.get(RunRecord, id) for id in (alice_run.run_id, bob_run.run_id)]
            assert all(row is not None for row in rows)
            assert all(json.loads(row.config_snapshot_json)["model"] == model
                       for row in rows)
            assert all("sk-test" not in row.config_snapshot_json for row in rows)
        keys.delete(alice, provider)

        with WorkerRunner(postgres_url, capability_reporter=lambda:
                          {"providers": ["extractive", "litellm"]}) as runner:
            assert runner.run_once()
            assert runner.run_once()
        with users.sessions() as session:
            alice_row = session.get(RunRecord, alice_run.run_id)
            bob_row = session.get(RunRecord, bob_run.run_id)
            assert alice_row.status == "failed"
            assert alice_row.error_code == "MODEL_PROVIDER_UNAVAILABLE"
            assert bob_row.status == "succeeded"
            assert "sk-test" not in (bob_row.result_json or "")
        assert len(seen) == 1
        assert seen[0]["api_key"] == "sk-test-bob-secret"
    finally:
        users.close()
