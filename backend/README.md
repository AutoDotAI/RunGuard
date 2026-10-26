# RunGuard API

The API stores accounts, worker configurations, queued runs, and logs in SQLite through SQLAlchemy and Alembic. New accounts have no workers or runs. Passwords are salted PBKDF2-HMAC-SHA256 hashes. Register and login return an eight-hour HS256 bearer JWT. Set a stable signing secret of at least 32 bytes so tokens remain valid after an API restart. Keep the secret outside Git.

Queued runs stay in SQLite until a separate worker process starts. Editing or archiving a worker preserves its earlier run snapshots and logs. The worker currently has no production Website Monitor or URL Summarizer handlers: queued runs are claimed and fail visibly with `HANDLER_UNAVAILABLE`. Real fetch and LiteLLM handlers are separate work.

The shared outbound boundary is `app.outbound.parse_public_url` and `app.outbound.fetch`. The API uses the same URL syntax policy when saving workers without doing DNS or network I/O. Worker fetches revalidate the URL, check every A/AAAA answer, connect to a validated pinned address with the original hostname for HTTPS SNI and certificate checks, disable redirects and ambient proxy use, and cap response reads at 1 MiB with a 10-second total fetch deadline. A stuck DNS lookup leaves at most one daemon resolver thread and makes later fetches fail closed until that lookup completes. Errors use sanitized categories (`URL_INVALID`, `DNS_ERROR`, `DESTINATION_BLOCKED`, `FETCH_TIMEOUT`, `FETCH_ERROR`, `RESPONSE_TOO_LARGE`) with no URL, query, headers, or response body in their messages. Real handlers in Issues #11 and #12 must call this shared fetcher.

**Public release gate:** Do not claim arbitrary user URLs are safe or enable public arbitrary-URL execution yet. Issue #23 must verify host-level outbound egress restrictions, including denial of private, loopback, link-local, and metadata destinations, in the actual deployment. A hostname allowlist alone is insufficient. Until that gate is verified, restrict execution to server-configured trusted public hosts plus enforced egress restrictions, as required by `_docs/old/SPEC.md` §10.2.

```bash
cd backend
uv sync --extra test
export RUNGUARD_AUTH_SECRET="$(openssl rand -hex 32)"
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In a second terminal, using the same `RUNGUARD_DATABASE_URL` as the API:

```bash
cd backend
uv run python -m app.workers.runner
```

The worker polls about once per second. `uv run python -m app.workers.runner --once` processes at most one queued run and exits. A second worker against the same local SQLite file exits with an error. On startup, runs left `running` by an interrupted worker become `failed` with `WORKER_INTERRUPTED`; queued runs remain eligible. The worker process and API must run on one host sharing one persistent SQLite volume. Stopping the worker leaves new runs queued while the API remains available.

The default database is `backend/data/runguard.db`; startup applies Alembic migrations through `0003_active_run_index`. The partial SQLite index permits only one queued or running run per worker, and enqueue uses a short write transaction to enforce the per-user active-run limit across API instances. To migrate explicitly, run `cd backend && uv run alembic upgrade head`. To choose another local file, set `RUNGUARD_DATABASE_URL=sqlite:////absolute/path/runguard.db` before migration and API startup. Reuse the same database URL and signing secret after restart. Do not regenerate the secret for each launch.

Register and use the returned `access_token` as `Authorization: Bearer <token>` on protected `/api/v1/*` routes:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/auth/register \
  -H 'content-type: application/json' \
  -d '{"email":"demo@example.com","password":"DemoPass123!"}'
```

Run `cd backend && uv run pytest -q` for isolated tests. The worker tests use temporary SQLite files and injected handlers, without external requests or model keys. API documentation is at `http://127.0.0.1:8000/docs`; the contract is in repository-root `openapi.yaml`.
