"""Offline provider failure and retry journey against an isolated PostgreSQL schema.

Set RUNGUARD_TEST_POSTGRES_URL to a disposable PostgreSQL database. The test
creates and drops only its own schema; all outbound/model calls are fake.
"""

import json
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import outbound
from app.db import RunRecord
from app.main import create_app
from app.workers.runner import WorkerRunner


SECRET = "test-only-secret-with-at-least-32-bytes"
BODY = (b"<title>Recovery report</title><article><p>" +
        b"The incident was detected, repaired, and reviewed by the response team. " * 3 +
        b"</p></article>")


@pytest.fixture
def postgres_url():
    configured = os.environ.get("RUNGUARD_TEST_POSTGRES_URL")
    if not configured:
        pytest.skip("RUNGUARD_TEST_POSTGRES_URL is not configured")
    url = make_url(configured)
    if url.get_backend_name() != "postgresql":
        pytest.fail("RUNGUARD_TEST_POSTGRES_URL must use PostgreSQL")
    schema = f"runguard_worker_failure_{uuid4().hex}"
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


def test_pg_failed_host_run_retries_original_model_after_setting_change(
        postgres_url, tmp_path, monkeypatch):
    monkeypatch.setenv("RUNGUARD_ALERT_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("RUNGUARD_CODEX_MODELS", "gpt-5.4")
    monkeypatch.setenv("RUNGUARD_MODEL_PROVIDER", "disabled")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(outbound, "fetch", lambda _url: outbound.FetchResponse(
        200, {"content-type": "text/html"}, BODY))
    codex = tmp_path / "codex"
    codex.write_text("#!/usr/bin/python3\nimport sys\n"
                     "_ = sys.stdin.read()\n"
                     "sys.stderr.write('private provider secret')\n"
                     "sys.exit(1)\n")
    codex.chmod(0o755)
    unexpected_claude = tmp_path / "claude-ran"
    claude = tmp_path / "claude"
    claude.write_text("#!/usr/bin/python3\nfrom pathlib import Path\n"
                      f"Path({str(unexpected_claude)!r}).touch()\n")
    claude.chmod(0o755)

    app = create_app(secret=SECRET, database_url=postgres_url)
    with TestClient(app) as client:
        app.state.ai_settings.capability_reader = lambda: {"codex", "claude"}
        registered = client.post("/api/v1/auth/register", json={
            "email": "failure@example.com", "password": "password123"})
        assert registered.status_code == 201
        headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
        original = {"provider_path": "backend_cli", "provider": "codex", "model": "gpt-5.4"}
        changed = {"provider_path": "backend_cli", "provider": "claude", "model": "default"}
        assert client.put("/api/v1/ai/settings", headers=headers, json=original).status_code == 200
        created = client.post("/api/v1/workers", headers=headers, json={
            "name": "Article", "kind": "url_summarizer", "url": "https://example.com/article"})
        assert created.status_code == 201
        worker_id = created.json()["id"]
        queued = client.post(f"/api/v1/workers/{worker_id}/runs", headers=headers)
        assert queued.status_code == 202
        first_id = queued.json()["run_id"]
        assert client.put("/api/v1/ai/settings", headers=headers, json=changed).status_code == 200

        with WorkerRunner(postgres_url, capability_reporter=lambda: {
                "providers": ["codex", "claude"]}) as runner:
            assert runner.run_once() is True
            assert runner.run_once() is False  # No automatic retry.
        failed = client.get(f"/api/v1/runs/{first_id}", headers=headers).json()
        assert failed["status"] == "failed"
        assert failed["error"]["code"] == "LLM_ERROR"
        assert "private provider secret" not in json.dumps(failed)
        first_logs = client.get(f"/api/v1/runs/{first_id}/logs", headers=headers).json()
        assert "private provider secret" not in json.dumps(first_logs)

        argv_file = tmp_path / "codex-args.json"
        codex.write_text("#!/usr/bin/python3\nimport json, sys\n"
                         f"open({str(argv_file)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
                         "_ = sys.stdin.read()\n"
                         "print('# Recovery report\\n- Detected.\\n- Repaired.\\n- Reviewed.')\n")
        retried = client.post(f"/api/v1/runs/{first_id}/retry", headers=headers)
        assert retried.status_code == 202
        retry_id = retried.json()["run_id"]
        with app.state.users.sessions() as session:
            source = session.get(RunRecord, first_id)
            retry = session.get(RunRecord, retry_id)
            assert retry.config_snapshot_json == source.config_snapshot_json
            assert json.loads(retry.config_snapshot_json) == original
        with WorkerRunner(postgres_url, capability_reporter=lambda: {
                "providers": ["codex", "claude"]}) as runner:
            assert runner.run_once() is True
        finished = client.get(f"/api/v1/runs/{retry_id}", headers=headers).json()
        assert finished["status"] == "succeeded"
        assert finished["result"]["model"] == "gpt-5.4"
        assert not unexpected_claude.exists()
        args = json.loads(argv_file.read_text())
        assert args[args.index("--model") + 1] == "gpt-5.4"
