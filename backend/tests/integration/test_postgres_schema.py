"""Exercise Alembic's fresh PostgreSQL schema in an isolated temporary schema.

Set RUNGUARD_TEST_POSTGRES_URL to a disposable PostgreSQL database URL. The
test creates and drops only its own uniquely named schema.
"""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def postgres_schema_url():
    database_url = os.environ.get("RUNGUARD_TEST_POSTGRES_URL")
    if not database_url:
        pytest.skip("RUNGUARD_TEST_POSTGRES_URL is not configured")
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        pytest.fail("RUNGUARD_TEST_POSTGRES_URL must use PostgreSQL")
    schema = f"runguard_schema_{uuid4().hex}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    try:
        yield isolated_url.render_as_string(hide_password=False), schema
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_fresh_postgres_alembic_schema_constraints_and_bigint_cursor(postgres_schema_url):
    database_url, schema = postgres_schema_url
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT current_schema()")) == schema
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == ScriptDirectory.from_config(config).get_current_head()
            assert connection.scalar(text("""
                SELECT data_type FROM information_schema.columns
                WHERE table_schema = :schema AND table_name = 'run_logs' AND column_name = 'id'
            """), {"schema": schema}) == "bigint"
            assert connection.scalar(text("""
                SELECT column_default FROM information_schema.columns
                WHERE table_schema = :schema AND table_name = 'run_logs' AND column_name = 'id'
            """), {"schema": schema}).startswith("nextval(")
            indexes = dict(connection.execute(text("""
                SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = :schema
            """), {"schema": schema}).all())
            assert "UNIQUE" in indexes["uq_runs_one_active_per_worker"]
            assert "status" in indexes["uq_runs_one_active_per_worker"]
            assert "queued" in indexes["uq_runs_one_active_per_worker"]
            assert "UNIQUE" in indexes["uq_alerts_open_fingerprint"]
            assert "open" in indexes["uq_alerts_open_fingerprint"]

        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO users(id,email,password_hash,created_at)
                VALUES ('user-1','test@example.com','hashed','2026-01-01T00:00:00Z')
            """))
            connection.execute(text("""
                INSERT INTO workers(id,owner_user_id,name,kind,url,config_json,created_at,updated_at)
                VALUES ('worker-1','user-1','Test','website_monitor','https://example.com/','{}',
                        '2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')
            """))
            connection.execute(text("""
                INSERT INTO runs(id,worker_id,owner_user_id,worker_name_snapshot,kind,url_snapshot,
                                 config_snapshot_json,status,created_at)
                VALUES ('run-1','worker-1','user-1','Test','website_monitor',
                        'https://example.com/','{}','queued','2026-01-01T00:00:00Z')
            """))

        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO runs(id,worker_id,owner_user_id,worker_name_snapshot,kind,url_snapshot,
                                 config_snapshot_json,status,created_at)
                VALUES ('run-2','worker-1','user-1','Test','website_monitor',
                        'https://example.com/','{}','running','2026-01-01T00:00:00Z')
            """))

        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO run_logs(run_id,owner_user_id,timestamp,level,event,message)
                VALUES ('missing-run','user-1','2026-01-01T00:00:00Z','INFO','TEST','orphan')
            """))

        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO run_logs(id,run_id,owner_user_id,timestamp,level,event,message)
                VALUES (2147483648,'run-1','user-1','2026-01-01T00:00:00Z','INFO','TEST','large cursor')
            """))
            assert connection.scalar(text("SELECT max(id) FROM run_logs")) == 2147483648
    finally:
        engine.dispose()
