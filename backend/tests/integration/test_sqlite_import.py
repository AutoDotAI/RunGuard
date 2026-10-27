"""Offline import checks against a disposable PostgreSQL schema.

Set RUNGUARD_TEST_POSTGRES_URL to a disposable local PostgreSQL database.
Each test owns and drops only its uniquely named schema.
"""

import os
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.auth import hash_password, verify_password
from app.db import UserRepository
from app.import_sqlite import ImportRefused, import_sqlite
from app.store import PersistentStore, Problem


@pytest.fixture
def postgres_url():
    database_url = os.environ.get("RUNGUARD_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("RUNGUARD_TEST_POSTGRES_URL is not configured")
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        pytest.fail("RUNGUARD_TEST_POSTGRES_URL must use PostgreSQL")
    schema = f"runguard_import_{uuid4().hex}"
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


def migrate_postgres(url: str) -> None:
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")


@pytest.fixture
def sqlite_source(tmp_path):
    path = tmp_path / "source.sqlite3"
    repository = UserRepository(f"sqlite:///{path}")
    repository.migrate()
    repository.close()
    password_hash = hash_password("correct-horse-battery")
    stamp = "2026-10-25T13:42:11Z"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executemany(
            "INSERT INTO users(id,email,password_hash,created_at) VALUES (?,?,?,?)",
            [("owner-a", "a@example.com", password_hash, stamp),
             ("owner-b", "b@example.com", hash_password("another-password"), stamp)],
        )
        connection.executemany(
            "INSERT INTO workers(id,owner_user_id,name,kind,url,config_json,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [("worker-a", "owner-a", "Article", "url_summarizer", "https://example.com/a",
              '{"limit":100}', stamp, stamp),
             ("worker-b", "owner-b", "Site", "website_monitor", "https://example.org/",
              "{}", stamp, stamp)],
        )
        run_sql = ("INSERT INTO runs(id,worker_id,owner_user_id,worker_name_snapshot,kind,"
                   "url_snapshot,config_snapshot_json,status,created_at,finished_at,result_json,"
                   "retry_of_run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)")
        connection.executemany(run_sql, [
            ("run-old", "worker-a", "owner-a", "Article", "url_summarizer",
             "https://example.com/a", '{"provider":"codex","model":"gpt-5"}',
             "failed", stamp, stamp, None, None),
            ("run-retry", "worker-a", "owner-a", "Article", "url_summarizer",
             "https://example.com/a", '{"provider":"codex","model":"gpt-5"}',
             "succeeded", stamp, stamp,
             '{"source_url":"https://example.com/a","page_title":"Article",'
             '"summary":"stored","model":"gpt-5","input_was_truncated":false}',
             "run-old"),
            ("run-b", "worker-b", "owner-b", "Site", "website_monitor",
             "https://example.org/", "{}", "succeeded", stamp, stamp,
             '{"http_status":200,"response_time_ms":10,"healthy":true}', None),
        ])
        connection.execute(
            "INSERT INTO run_logs(id,run_id,owner_user_id,timestamp,level,event,message) "
            "VALUES (2147483648,?,?,?,?,?,?)",
            ("run-old", "owner-a", stamp, "ERROR", "MODEL_FAILED", "safe failure"),
        )
        connection.execute(
            "INSERT INTO alerts(id,owner_user_id,kind,severity,status,title,run_id,symptom,"
            "fingerprint,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("alert-a", "owner-a", "repeated_run_failure", "warning", "open", "Failed runs",
             "run-old", "A failure", "fingerprint-a", stamp),
        )
        connection.execute(
            "INSERT INTO responder_incidents(id,alert_id,owner_user_id,run_id,evidence_json,"
            "symptom,likely_cause,recommendation,decision_source,status,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("incident-a", "alert-a", "owner-a", "run-old", '{"run":"run-old"}',
             "A failure", "model_error", "inspect", "rules", "recorded", stamp, stamp),
        )
        connection.execute(
            "INSERT INTO responder_actions(id,incident_id,alert_id,actor,policy,action,decision,"
            "reason,before_status,result,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("action-a", "incident-a", "alert-a", "responder", "bounded", "none",
             "denied", "not_demo", "failed", "recorded", stamp, stamp),
        )
        connection.execute(
            "INSERT INTO incident_analyses(id,incident_id,owner_user_id,attempt,provider,status,"
            "evidence_ids_json,started_at) VALUES (?,?,?,?,?,?,?,?)",
            ("analysis-a", "incident-a", "owner-a", 1, "disabled", "skipped",
             '["run-old"]', stamp),
        )
    return path


def test_import_preserves_history_login_and_log_sequence(sqlite_source, postgres_url, tmp_path):
    migrate_postgres(postgres_url)
    backup = tmp_path / "private-backup.sqlite3"
    source_bytes = sqlite_source.read_bytes()
    counts = import_sqlite(sqlite_source, postgres_url, backup)
    assert counts == {"users": 2, "workers": 2, "runs": 3, "run_logs": 1,
                      "alerts": 1, "responder_incidents": 1, "responder_actions": 1,
                      "incident_analyses": 1}
    assert backup.exists() and backup.stat().st_mode & 0o777 == 0o600
    assert sqlite_source.read_bytes() == source_bytes

    repository = UserRepository(postgres_url)
    try:
        user = repository.by_email("a@example.com")
        assert user["id"] == "owner-a"
        assert verify_password("correct-horse-battery", user["password_hash"])
        assert not verify_password("wrong-password", user["password_hash"])
        store = PersistentStore(repository)
        assert store.get_run("owner-a", "run-retry").retry_of_run_id == "run-old"
        with pytest.raises(Problem) as denied:
            store.get_run("owner-b", "run-retry")
        assert denied.value.status == 404
        assert store.list_runs("owner-b", None, None, 1, 20).total == 1
        assert store.list_runs("owner-a", None, None, 1, 20).total == 2
        assert [item.id for item in store.get_logs("owner-a", "run-old", 0).items] == [2147483648]
        with repository.engine.begin() as connection:
            row = connection.execute(text(
                "SELECT config_snapshot_json, created_at, finished_at, retry_of_run_id "
                "FROM runs WHERE id='run-retry' ")).one()
            assert row == ('{"provider":"codex","model":"gpt-5"}',
                           "2026-10-25T13:42:11Z", "2026-10-25T13:42:11Z", "run-old")
            assert connection.scalar(text("SELECT count(*) FROM responder_actions")) == 1
            assert connection.scalar(text("SELECT count(*) FROM incident_analyses")) == 1
            new_log_id = connection.scalar(text(
                "INSERT INTO run_logs(run_id,owner_user_id,timestamp,level,event,message) "
                "VALUES ('run-old','owner-a','2026-10-25T13:42:12Z','INFO','NEXT','safe') "
                "RETURNING id"))
            assert new_log_id == 2147483649
    finally:
        repository.close()


def test_import_refuses_nonempty_target_without_touching_existing_rows(sqlite_source, postgres_url, tmp_path):
    migrate_postgres(postgres_url)
    engine = create_engine(postgres_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO users(id,email,password_hash,created_at) "
                                    "VALUES ('existing','x@example.com','existing','2026-01-01')"))
        with pytest.raises(ImportRefused, match="already contains data"):
            import_sqlite(sqlite_source, postgres_url, tmp_path / "backup.sqlite3")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM users")) == 1
            assert connection.scalar(text("SELECT count(*) FROM runs")) == 0
    finally:
        engine.dispose()


def test_import_refuses_unmigrated_target(sqlite_source, postgres_url, tmp_path):
    with pytest.raises(ImportRefused, match="not been migrated"):
        import_sqlite(sqlite_source, postgres_url, tmp_path / "backup.sqlite3")


def test_import_refuses_unknown_sqlite_revision(sqlite_source, postgres_url, tmp_path):
    migrate_postgres(postgres_url)
    with sqlite3.connect(sqlite_source) as connection:
        connection.execute("UPDATE alembic_version SET version_num='unknown_revision'")
    with pytest.raises(ImportRefused, match="unknown Alembic revision"):
        import_sqlite(sqlite_source, postgres_url, tmp_path / "backup.sqlite3")
    engine = create_engine(postgres_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM users")) == 0
    finally:
        engine.dispose()


def test_import_rolls_back_broken_owner_relationship(sqlite_source, postgres_url, tmp_path):
    migrate_postgres(postgres_url)
    # SQLite FKs remain valid, but the denormalized owner is inconsistent.
    with sqlite3.connect(sqlite_source) as connection:
        connection.execute("UPDATE runs SET owner_user_id='owner-b' WHERE id='run-old'")
    with pytest.raises(ImportRefused, match="owner isolation"):
        import_sqlite(sqlite_source, postgres_url, tmp_path / "backup.sqlite3")
    engine = create_engine(postgres_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM users")) == 0
    finally:
        engine.dispose()
