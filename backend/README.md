# RunGuard API

The API stores accounts, worker configurations, queued runs, and logs in SQLite through SQLAlchemy and Alembic. New accounts have no workers or runs. Passwords are salted PBKDF2-HMAC-SHA256 hashes. Register and login return an eight-hour HS256 bearer JWT. Set a stable signing secret of at least 32 bytes so tokens remain valid after an API restart. Keep the secret outside Git.

Queued runs remain queued until the dedicated execution worker is implemented. Editing or archiving a worker preserves its earlier run snapshots and logs.

```bash
cd backend
uv sync --extra test
export RUNGUARD_AUTH_SECRET="$(openssl rand -hex 32)"
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The default database is `backend/data/runguard.db`; startup applies Alembic migrations through `0003_active_run_index`. The partial SQLite index permits only one queued or running run per worker, and enqueue uses a short write transaction to enforce the per-user active-run limit across API instances. To migrate explicitly, run `cd backend && uv run alembic upgrade head`. To choose another local file, set `RUNGUARD_DATABASE_URL=sqlite:////absolute/path/runguard.db` before migration and API startup. Reuse the same database URL and signing secret after restart. Do not regenerate the secret for each launch.

Register and use the returned `access_token` as `Authorization: Bearer <token>` on protected `/api/v1/*` routes:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/auth/register \
  -H 'content-type: application/json' \
  -d '{"email":"demo@example.com","password":"DemoPass123!"}'
```

Run `uv run pytest -q` from `backend/` for isolated tests. API documentation is at `http://127.0.0.1:8000/docs`; the contract is in repository-root `openapi.yaml`.
