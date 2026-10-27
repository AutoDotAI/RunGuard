# RunGuard API and worker

RunGuard uses PostgreSQL through SQLAlchemy and Alembic for local accounts,
workers, runs, logs, AI settings, alerts, and incident audit. FastAPI handles
registration and login with salted password hashes and expiring bearer JWTs.
A separate Python process claims queued runs; stopping it leaves new runs
queued. Start exactly one worker against the local database. Keep
`RUNGUARD_AUTH_SECRET` stable across restarts and outside Git.

The worker implements `website_monitor` and `url_summarizer`. The latter can
use a host Codex CLI login by default, host Claude CLI, or a configured
server-side LiteLLM key. A missing provider is reported separately from a
failed attempted model request. No provider fallback or automatic job retry
occurs. See [model provider setup](../docs/model-providers.md).

## Local source run

Set `RUNGUARD_DATABASE_URL` to the loopback PostgreSQL URL and a stable
`RUNGUARD_AUTH_SECRET` in your shell. The local Compose service exposes
PostgreSQL on `127.0.0.1:5432` by default. Then:

```bash
cd backend
uv sync --locked --extra test
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

In another terminal with the same environment, run `cd backend && uv run
python -m app.workers.runner`. Use `--once` to process at most one queued job.
Only one worker may hold the database advisory lock; interrupted running jobs
become visible `WORKER_INTERRUPTED` failures during reconciliation. The API
and worker share the PostgreSQL database and should not be started against
the old SQLite volume after cutover. `GET /health` checks readiness.

Register and use the returned `access_token` as `Authorization: Bearer
<token>` on protected `/api/v1/*` routes:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/auth/register \
  -H 'content-type: application/json' \
  -d '{"email":"demo@example.com","password":"DemoPass123!"}'
```

Run `uv run pytest -q` from `backend/`; PostgreSQL integration tests use a
disposable database, while HTTP/model tests inject fakes and need no live
provider account. The repository-root [OpenAPI contract](../openapi.yaml)
defines routes and bodies. Interactive API docs are at
<http://127.0.0.1:8000/docs> when the API is running.

## URL and incident boundaries

The shared outbound boundary is `app.outbound.parse_public_url` and
`app.outbound.fetch`. API save validation does no DNS or network I/O. Worker
fetches check every A/AAAA answer, connect to a validated pinned address
with the original HTTPS hostname, disable redirects and ambient proxies,
and cap reads at 1 MiB and ten seconds. Errors have sanitized categories
without URL query, headers, or response body. Run
`uv run pytest -q tests/test_outbound.py` to check local SSRF policy. Public
arbitrary-URL execution remains outside the local milestone; an externally
verified host firewall and allowlist would be required before deployment.

Owner-scoped alerts are durable. The optional telemetry profile routes
aggregate notifications to local Alertmanager and an operator inbox. The
bounded responder can resume only its own isolated paused demo worker; it
cannot retry user jobs. See [observability](../docs/observability.md) and
[incident investigation](../docs/incident-ai.md).
