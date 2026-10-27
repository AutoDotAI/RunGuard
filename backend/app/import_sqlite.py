"""Offline, one-time import of the preserved SQLite database into PostgreSQL.

Run only with the API and job worker stopped. The source is opened read-only and
copied using SQLite's backup API before any PostgreSQL writes are attempted.
"""

import argparse
import os
import sqlite3
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import MetaData, Table, create_engine, func, inspect, select, text, update
from sqlalchemy.engine import Connection, make_url


BASE_TABLES = (
    "users", "workers", "runs", "run_logs", "alerts",
    "responder_incidents", "responder_actions", "incident_analyses",
)
# Later local settings are copied if a SQLite source already has them. Runtime
# heartbeat state is deliberately not imported; the new worker owns its lease.
OPTIONAL_TABLES = ("ai_settings",)
SOURCE_TABLES = frozenset((*BASE_TABLES, *OPTIONAL_TABLES))
# A paused demo or stale worker lease must not survive a database cutover.
TRANSIENT_TABLES = frozenset(("demo_control_state", "worker_heartbeat"))
SYSTEM_TABLES = frozenset(("alembic_version", "sqlite_sequence", *TRANSIENT_TABLES))
COPY_ORDER = (*BASE_TABLES, *OPTIONAL_TABLES)


class ImportRefused(ValueError):
    """The source or target cannot be imported without risking existing data."""


def _backup_sqlite(source: Path, backup: Path) -> None:
    if not source.is_file() or backup.exists() or source.resolve() == backup.resolve():
        raise ImportRefused("Source must exist and backup must be a new, separate file")
    if not backup.parent.is_dir():
        raise ImportRefused("Backup directory must already exist")
    descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True) as original:
            original.execute("PRAGMA query_only=ON")
            with sqlite3.connect(backup) as snapshot:
                original.backup(snapshot)
        os.chmod(backup, 0o600)
    except Exception:
        backup.unlink(missing_ok=True)
        raise


def _source_tables(snapshot: sqlite3.Connection) -> dict[str, list[str]]:
    integrity = snapshot.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or integrity[0] != "ok":
        raise ImportRefused("SQLite backup failed integrity_check")
    if snapshot.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ImportRefused("SQLite backup contains broken foreign keys")
    names = {row[0] for row in snapshot.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    unexpected = names - SOURCE_TABLES - SYSTEM_TABLES
    if unexpected or not set(BASE_TABLES).issubset(names):
        raise ImportRefused("SQLite backup has missing or unrecognized application tables")
    if "alembic_version" not in names:
        raise ImportRefused("SQLite backup has no Alembic revision")
    source_versions = [row[0] for row in snapshot.execute("SELECT version_num FROM alembic_version")]
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    try:
        known_revision = (len(source_versions) == 1
                          and scripts.get_revision(source_versions[0]) is not None)
    except CommandError:
        known_revision = False
    if not known_revision:
        raise ImportRefused("SQLite backup has an unknown Alembic revision")
    return {name: [row[1] for row in snapshot.execute(f'PRAGMA table_info("{name}")')]
            for name in names & SOURCE_TABLES}


def _verify_target(connection: Connection, tables: dict[str, list[str]]) -> dict[str, Table]:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    head = ScriptDirectory.from_config(config).get_current_head()
    inspector = inspect(connection)
    names = set(inspector.get_table_names())
    if "alembic_version" not in names:
        raise ImportRefused("PostgreSQL target has not been migrated")
    versions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    if versions != [head]:
        raise ImportRefused("PostgreSQL target is not at the current Alembic head")
    if not set(tables).issubset(names):
        raise ImportRefused("PostgreSQL target is missing a SQLite data table")
    if not set(BASE_TABLES).issubset(names):
        raise ImportRefused("PostgreSQL target schema is incomplete")
    # All application tables, including singleton control tables, must be empty.
    quote = connection.dialect.identifier_preparer.quote
    for name in names - {"alembic_version"}:
        if connection.scalar(text(f'SELECT EXISTS (SELECT 1 FROM {quote(name)} LIMIT 1)')):
            raise ImportRefused("PostgreSQL target already contains data")
    metadata = MetaData()
    target = {name: Table(name, metadata, autoload_with=connection) for name in tables}
    for name, source_columns in tables.items():
        target_columns = set(target[name].columns.keys())
        if not set(source_columns).issubset(target_columns):
            raise ImportRefused(f"PostgreSQL table {name} lacks SQLite columns")
        for column in target[name].columns:
            if (column.name not in source_columns and not column.nullable
                    and column.default is None and column.server_default is None
                    and not column.autoincrement):
                raise ImportRefused(f"PostgreSQL table {name} needs an unmapped column")
    return target


def _copy_table(snapshot: sqlite3.Connection, connection: Connection, table: Table,
                columns: list[str], *, defer_retry: bool = False) -> int:
    quoted_columns = ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
    query = f'SELECT {quoted_columns} FROM "{table.name}"'
    cursor = snapshot.execute(query)
    count = 0
    while rows := cursor.fetchmany(500):
        records = [dict(zip(columns, row, strict=True)) for row in rows]
        if defer_retry:
            for record in records:
                record["retry_of_run_id"] = None
        connection.execute(table.insert(), records)
        count += len(records)
    return count


def _verify_ownership(connection: Connection) -> None:
    checks = (
        "SELECT 1 FROM runs r JOIN workers w ON w.id=r.worker_id "
        "WHERE r.owner_user_id<>w.owner_user_id LIMIT 1",
        "SELECT 1 FROM run_logs l JOIN runs r ON r.id=l.run_id "
        "WHERE l.owner_user_id<>r.owner_user_id LIMIT 1",
        "SELECT 1 FROM alerts a JOIN runs r ON r.id=a.run_id "
        "WHERE a.owner_user_id<>r.owner_user_id LIMIT 1",
        "SELECT 1 FROM responder_incidents i JOIN alerts a ON a.id=i.alert_id "
        "WHERE i.owner_user_id<>a.owner_user_id OR i.run_id<>a.run_id LIMIT 1",
        "SELECT 1 FROM incident_analyses n JOIN responder_incidents i ON i.id=n.incident_id "
        "WHERE n.owner_user_id<>i.owner_user_id LIMIT 1",
    )
    for query in checks:
        if connection.scalar(text(query)) is not None:
            raise ImportRefused("Imported records violate owner isolation")


def _reset_sequences(connection: Connection) -> None:
    inspector = inspect(connection)
    quote = connection.dialect.identifier_preparer.quote
    for table_name in inspector.get_table_names():
        for column in inspector.get_columns(table_name):
            sequence = connection.scalar(text("SELECT pg_get_serial_sequence(:table, :column)"),
                                         {"table": table_name, "column": column["name"]})
            if sequence is None:
                continue
            current = connection.scalar(text(
                f'SELECT max({quote(column["name"])}) FROM {quote(table_name)}'))
            connection.scalar(text("SELECT setval(CAST(:sequence AS regclass), :value, :called)"),
                              {"sequence": sequence, "value": current or 1,
                               "called": current is not None})


def import_sqlite(source_path: Path, target_url: str, backup_path: Path) -> dict[str, int]:
    """Copy an offline SQLite source into an empty migrated PostgreSQL target.

    The backup remains on disk if validation or import fails. PostgreSQL data
    inserts are one transaction and roll back on any failed check.
    """
    if make_url(target_url).drivername != "postgresql+psycopg":
        raise ImportRefused("Target must use a postgresql+psycopg URL")
    source_path = Path(source_path)
    backup_path = Path(backup_path)
    _backup_sqlite(source_path, backup_path)
    engine = create_engine(target_url, pool_pre_ping=True)
    try:
        with sqlite3.connect(f"{backup_path.resolve().as_uri()}?mode=ro", uri=True) as snapshot:
            snapshot.row_factory = sqlite3.Row
            source_tables = _source_tables(snapshot)
            with engine.begin() as target_connection:
                target_tables = _verify_target(target_connection, source_tables)
                counts: dict[str, int] = {}
                for name in COPY_ORDER:
                    if name not in source_tables:
                        continue
                    counts[name] = _copy_table(snapshot, target_connection, target_tables[name],
                                               source_tables[name], defer_retry=name == "runs")
                # Retry references are self-FKs; the first pass inserted every run.
                if "retry_of_run_id" in source_tables["runs"]:
                    runs = target_tables["runs"]
                    for run_id, parent in snapshot.execute(
                            "SELECT id, retry_of_run_id FROM runs WHERE retry_of_run_id IS NOT NULL"):
                        target_connection.execute(update(runs).where(runs.c.id == run_id).values(
                            retry_of_run_id=parent))
                for name, expected in counts.items():
                    actual = target_connection.scalar(select(func.count()).select_from(target_tables[name]))
                    if actual != expected:
                        raise ImportRefused(f"PostgreSQL row count mismatch in {name}")
                _verify_ownership(target_connection)
                _reset_sequences(target_connection)
                return counts
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline one-time SQLite to PostgreSQL import")
    parser.add_argument("--source", required=True, type=Path, help="Stopped app's SQLite database file")
    parser.add_argument("--backup", required=True, type=Path, help="New private SQLite backup path")
    parser.add_argument("--offline-confirmed", action="store_true",
                        help="Confirm API and worker are stopped before import")
    args = parser.parse_args()
    if not args.offline_confirmed:
        parser.error("Stop API and worker, then pass --offline-confirmed")
    target_url = os.environ.get("RUNGUARD_DATABASE_URL")
    if not target_url:
        parser.error("RUNGUARD_DATABASE_URL must point to the empty migrated PostgreSQL database")
    try:
        counts = import_sqlite(args.source, target_url, args.backup)
    except ImportRefused as exc:
        parser.exit(2, f"Import refused: {exc}\n")
    print("Imported " + ", ".join(f"{name}={count}" for name, count in counts.items()))
    print(f"SQLite backup retained at {args.backup}")


if __name__ == "__main__":
    main()
