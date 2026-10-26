# RunGuard API

The API stores accounts, worker configurations, queued runs, and logs in SQLite through SQLAlchemy and Alembic. New accounts have no workers or runs. Passwords are salted PBKDF2-HMAC-SHA256 hashes. Register and login return an eight-hour HS256 bearer JWT. Set a stable signing secret of at least 32 bytes so tokens remain valid after an API restart. Keep the secret outside Git.

Queued runs stay in SQLite until a separate worker process starts. Editing or archiving a worker preserves its earlier run snapshots and logs. Website Monitor uses the shared pinned fetcher. URL Summarizer uses that fetcher and one model-provider call. The host worker defaults to the existing Codex CLI login; set `RUNGUARD_MODEL_PROVIDER=claude` for Claude CLI or `RUNGUARD_MODEL_PROVIDER=litellm` with `LLM_MODEL` and a server-side provider credential for the API-key path. The Compose worker defaults to `disabled` because subscription credentials are never mounted into containers. A missing or failed provider produces a terminal `LLM_ERROR` without switching models or retrying. See [`../docs/model-providers.md`](../docs/model-providers.md) for the exact host and Compose commands.

The API also evaluates owner-scoped user-impact alerts every ten seconds and exposes them at authenticated `GET /api/v1/alerts`. Two recent failed runs for one account or a run stalled behind an unhealthy worker open a durable alert; recovery resolves it without deleting its history. The dashboard polls this endpoint. The optional local observability profile routes only aggregate kind/severity notifications to Alertmanager and the loopback operator inbox; see [`../docs/observability.md`](../docs/observability.md).

The separate `python -m app.responder once` command inspects persisted open alerts and records a deterministic offline incident and action audit. It never retries jobs or calls a model. Only the isolated demo project can resume its single bound paused fixture through `DemoControl`; other environments record a read-only recommendation. Use the complete local drill in [`../docs/observability.md`](../docs/observability.md). An operator may also request [optional host CLI analysis](../docs/incident-ai.md) of one persisted incident; that advisory path has no remediation authority.

The shared outbound boundary is `app.outbound.parse_public_url` and `app.outbound.fetch`. The API uses the same URL syntax policy when saving workers without doing DNS or network I/O. Worker fetches revalidate the URL, check every A/AAAA answer, connect to a validated pinned address with the original hostname for HTTPS SNI and certificate checks, disable redirects and ambient proxy use, and cap response reads at 1 MiB with a 10-second total fetch deadline, including slow response headers and body streams. A stuck DNS lookup or HTTP stream leaves at most one daemon thread in its respective slot and makes later fetches fail closed until that operation completes. Errors use sanitized categories (`URL_INVALID`, `DNS_ERROR`, `DESTINATION_BLOCKED`, `FETCH_TIMEOUT`, `FETCH_ERROR`, `RESPONSE_TOO_LARGE`) with no URL, query, headers, or response body in their messages. Both handlers use this shared fetcher.

**Public release gate:** Do not claim arbitrary user URLs are safe or enable public arbitrary-URL execution yet. Issue #23 must verify host-level outbound egress restrictions, including denial of private, loopback, link-local, and metadata destinations, in the actual deployment. A hostname allowlist alone is insufficient. Until that gate is verified, restrict execution to server-configured trusted public hosts plus enforced egress restrictions, as required by `_docs/old/SPEC.md` §10.2.

The local production template in [`../deploy/README.md`](../deploy/README.md)
enables `RUNGUARD_PUBLIC_MODE=1`, requires up to five exact
`RUNGUARD_ALLOWED_HOSTS`, and applies that list at save and execution time.
Public mode also limits registration (5 per client IP per 5 minutes), login
(10 per client IP per 5 minutes), and run creation (10 per user per minute),
returning `429 RATE_LIMITED` with `Retry-After`. These single-process limits
reset after an API restart. `/health` checks the SQLite connection and returns
`503 NOT_READY` if it cannot be read. The production Compose topology is a
local readiness template until external host/HTTPS/egress QA is complete.

```bash
cd backend
uv sync --locked --extra test
export RUNGUARD_AUTH_SECRET="$(openssl rand -hex 32)"
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --no-access-log
```

In a second terminal, using the same `RUNGUARD_DATABASE_URL` as the API:

```bash
cd backend
uv run python -m app.workers.runner
```

The worker polls about once per second. `uv run python -m app.workers.runner --once` processes at most one queued run and exits. A second worker against the same local SQLite file exits with an error. On startup, runs left `running` by an interrupted worker become `failed` with `WORKER_INTERRUPTED`; queued runs remain eligible. The worker process and API must run on one host sharing one persistent SQLite volume. Stopping the worker leaves new runs queued while the API remains available.

The default database is `backend/data/runguard.db`; startup applies Alembic migrations through `0006_incident_analysis`. The partial SQLite index permits only one queued or running run per worker, and enqueue uses a short write transaction to enforce the per-user active-run limit across API instances. To migrate explicitly, run `cd backend && uv run alembic upgrade head`. To choose another local file, set `RUNGUARD_DATABASE_URL=sqlite:////absolute/path/runguard.db` before migration and API startup. Reuse the same database URL and signing secret after restart. Do not regenerate the secret for each launch.

Register and use the returned `access_token` as `Authorization: Bearer <token>` on protected `/api/v1/*` routes:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/auth/register \
  -H 'content-type: application/json' \
  -d '{"email":"demo@example.com","password":"DemoPass123!"}'
```

Run `cd backend && uv run pytest -q` for isolated tests. The worker tests use temporary SQLite files and injected fetch/model clients, without external requests or model keys. API documentation is at `http://127.0.0.1:8000/docs`; the contract is in repository-root `openapi.yaml`.

To run the complete offline API → SQLite → worker integration journeys from a fresh checkout:

```bash
cd backend
uv sync --locked --extra test
uv run pytest -q tests/integration
```

Each integration test migrates its own temporary SQLite file. It fakes only outbound website responses and model completions; no live website, model key, or external auth project is needed.
