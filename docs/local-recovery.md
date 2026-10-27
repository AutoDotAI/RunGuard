# Local database backup and recovery

RunGuard is local only. Keep the normal web/API/worker stack stopped during the
one-time SQLite import. Keep the original `runguard_runguard_data` Docker
volume and the existing `.env` signing secret. Never use `docker compose down
--volumes` on the normal project.

## 1. Preserve the old SQLite data before import

The old SQLite-era `.env` may contain only `RUNGUARD_AUTH_SECRET`. Compose now
requires a PostgreSQL password even for `down` and `config`. From the repository
root, add a **stable** password to that existing file before any Compose command.
The helper preserves the auth secret, prints neither secret, and refuses a
missing or invalid auth secret. Keep this `.env` for subsequent starts:

```bash
python3 scripts/ensure_local_postgres_env.py
docker compose config --quiet
```

Then stop the normal project without deleting volumes:

```bash
docker compose down
docker volume inspect runguard_runguard_data
mkdir -p -m 700 private-backups
docker image inspect runguard-api:local >/dev/null || docker build -t runguard-api:local ./backend
docker run --rm --user 0:0 \
  --mount type=volume,source=runguard_runguard_data,target=/old,readonly \
  --mount type=bind,source="$PWD",target=/src,readonly \
  --mount type=bind,source="$PWD/private-backups",target=/out \
  runguard-api:local python /src/deploy/backup.py \
  --source /old/runguard.db --destination-dir /out \
  --owner-uid "$(id -u)" --owner-gid "$(id -g)"
```

The command uses SQLite's backup API, including committed WAL data. It reads the
old volume read-only and writes a private 0600 snapshot under
`private-backups/`. Record the reported filename as `SQLITE_SNAPSHOT`.
If the volume or database file is absent, stop; do not initialize an empty
replacement and call it a successful migration.

The one-time importer in task 44 takes a second consistent backup of that
snapshot. Start **only PostgreSQL**, then import into an empty database already
upgraded to the current Alembic head. The API and worker must stay stopped:

```bash
docker compose up -d postgres
set -a
. ./.env
set +a
export RUNGUARD_DATABASE_URL="postgresql+psycopg://runguard:$RUNGUARD_POSTGRES_PASSWORD@127.0.0.1:5432/runguard"
export SQLITE_SOURCE="$PWD/private-backups/REPLACE_WITH_REPORTED_FILENAME.db"
export SQLITE_BACKUP="$PWD/private-backups/import-source-snapshot.sqlite3"
(cd backend && uv run --locked alembic upgrade head)
OFFLINE_CONFIRMED=true make postgres-import
```

Use the configured `RUNGUARD_POSTGRES_USER`, `RUNGUARD_POSTGRES_DB`, and
`RUNGUARD_POSTGRES_PORT` if they differ from the defaults shown above.
The Make target invokes `python -m app.import_sqlite --source ... --backup ...
--offline-confirmed` and refuses to run without the explicit offline guard and
absolute paths. The importer refuses a nonempty or incompatible PostgreSQL
target and retains
the source snapshot on failure. Keep the original volume even after successful
verification. Keep the same `RUNGUARD_AUTH_SECRET` so existing bearer tokens
and account sessions retain their expected signing key.
Starting an empty PostgreSQL service is preparation, not proof that the old
accounts were imported; verify restored login and history before using it.

## 2. Back up and restore PostgreSQL locally

Run these commands with the PostgreSQL service healthy. A custom-format archive
is created with private 0600 permissions. The restore command requires a **new
database name** and refuses the active database or any existing database:

```bash
ARCHIVE_PATH=private-backups/runguard-pg.dump make postgres-backup
ARCHIVE_PATH=private-backups/runguard-pg.dump RESTORE_DB=runguard_restore_check make postgres-restore
```

The script uses `pg_dump` and `pg_restore` from the PostgreSQL 16 service
container. It does not overwrite an existing archive or database. Archive
contents include account password hashes, workers, runs, logs, alerts, and
incident records. Protect the archive and `.env` as private data. A restored
database also needs the same `RUNGUARD_AUTH_SECRET`; the signing secret is
**not** stored in PostgreSQL or in the dump.

For a safe automated practice restore, run the disposable smoke below. It
creates a separate Compose project, writes a test account/run/log, restores into
a fresh database, checks login and historical run/log API reads, runs the
guarded SQLite importer against another fresh database, and deletes only its
own test volume:

```bash
make install-backend
make postgres-recovery-smoke
```

Before using a restored copy for real work, point a local API at that fresh
database and verify a known account login, owned historical run, and run logs.
Compare counts in `users`, `workers`, `runs`, and `run_logs` with the
source; keep the source archive until this check passes. Stop the API/worker
before switching the configured database URL.

## 3. Recovery limit

If the PostgreSQL cutover fails **before any new PostgreSQL writes**, the stopped
site can be returned to the retained SQLite backup after restoring the former
SQLite application configuration. Once users create or change data in
PostgreSQL, switching back to the old SQLite snapshot **loses every newer
PostgreSQL write**. Restore a recent PostgreSQL dump instead. A container image
rollback alone does not reverse a database migration.
