# RunGuard

RunGuard is a local React/FastAPI application with a separate Python job worker,
PostgreSQL, and optional Grafana/Prometheus/Loki/Tempo observability. It is **not
publicly deployed**. Public hosting and CD remain deferred under
[issue #34](https://github.com/AutoDotAI/RunGuard/issues/34). The approved
scope is in [_docs/old/SPEC.md](_docs/old/SPEC.md); work and independent QA
are tracked in [AutoDotAI issues](https://github.com/AutoDotAI/RunGuard/issues).
The existing Lovable frontend stays in `frontend/`; see
[_docs/lovable-sync.md](_docs/lovable-sync.md).

## Start locally

Requirements: Docker Engine with Compose and free host ports 3000, 8000, and
5432. A fresh clone needs no `.env`, Python, Node.js, API key, or exported
variable for the local website:

```bash
docker compose up --build
```

Compose generates unique PostgreSQL and JWT credentials in a private named
volume and reuses them after restart or recreation. An existing ignored `.env`
and populated PostgreSQL volume keep their original credentials and data; a
missing or mismatched old credential stops startup instead of replacing it.
Do not remove the normal project's credential or PostgreSQL volumes. Set
`RUNGUARD_POSTGRES_PORT` only if host port 5432 is occupied.
The UI is <http://127.0.0.1:3000/signup>. Its browser API requests use the same
origin, so only port 3000 needs browser or VS Code forwarding. Direct API access
at <http://127.0.0.1:8000> remains available for local diagnostics.
`migrate` must exit successfully before the API and worker start. All published
ports bind to loopback. `docker compose down` stops services and retains the
PostgreSQL and preserved SQLite volumes; **never use `down --volumes` for the
normal project**. Start exactly one job worker against the database.

The older SQLite volume is preserved as the source of a verified one-time
import. A fresh Compose project creates a new PostgreSQL database; it does
not silently import a different project's SQLite data. Follow
[local recovery](docs/local-recovery.md) for the offline backup/import and
PostgreSQL restore commands before relying on historical accounts and runs.
The guarded Makefile commands are `make postgres-import` (requires
`OFFLINE_CONFIRMED=true` and explicit absolute source/backup paths),
`make postgres-backup`, `make postgres-restore`, and
`make postgres-recovery-smoke`.

## Model selection

A fresh account uses **local demo — no AI model** by default for URL
Summarizer. The Compose worker copies a short excerpt from bounded readable
HTML, so signup and a first summary need no model login or API key. This is
an extractive demo, not a Codex, Claude, or LiteLLM response. The AI settings
page shows when the worker is ready and refreshes availability during startup.

The AI settings page shows all four paths after a clean Compose start: local
demo, Codex CLI, Claude CLI, and API key. Choose **API key**, then OpenAI or
Anthropic, enter your own key, choose an approved model, and save settings.
RunGuard stores the key encrypted for your account; it does not return it in
the browser or require an `.env` edit or Compose restart. A real model call
may incur provider charges when you run a Summarizer.

Codex and Claude need a signed-in CLI and host worker on the same machine.
After `docker compose up --build`, run `make install-backend` and then
`make worker-codex` or `make worker-claude` in another terminal. That target
stops the container worker and reads the private local database credential.
Use **Refresh providers** on the settings page after it starts. The API and
browser never receive CLI login files. See [model provider setup](docs/model-providers.md).
A model subscription is not an API key. Tests make no paid model calls.

## Commands and checks

`make help` lists the tested local targets. Install with `make install` (Python
3.12+, `uv`, Node.js and npm), then use `make test`, `make typecheck`,
`make build`, and `make security-check`. `make check` runs those checks with
locked dependencies. `make compose-config`, `make compose-up`,
`make compose-status`, `make compose-logs`, and `make compose-down` wrap the
local stack. `make postgres-smoke` uses an isolated disposable project to
check database restart persistence. `make compose-smoke` exercises a real
API→worker journey and contacts `example.com`; offline backend tests use fake
HTTP and model adapters. No command in this repository deploys publicly.
`make first-run-smoke` checks a copied checkout with no `.env`, restart
persistence, and a synthetic old `.env`/database upgrade in disposable Compose
projects.

The [local script inventory](docs/local-scripts.md) explains each optional
check and recovery helper; none is required for a recipient's normal site
startup.

The [CI workflow](.github/workflows/ci.yml) runs the backend suite against a
temporary PostgreSQL 16 service, then checks the PostgreSQL integration files
explicitly so they cannot pass by skipping for a missing database URL. To run
the same database checks locally, supply a **disposable** PostgreSQL database
URL (each test creates and drops only its own uniquely named schema):

```bash
make install-backend
RUNGUARD_TEST_POSTGRES_URL='postgresql+psycopg://USER:PASSWORD@127.0.0.1:PORT/TEST_DB' make test-postgres
```

`make test-backend` runs the full backend suite, and `make test-frontend` runs
the React tests. CI also runs TypeScript checking and builds both images; it
uses fake HTTP/model clients and needs no provider API key or CLI login.

The source frontend runs with `cd frontend && npm ci --no-audit --no-fund &&
npm run dev`; copy `frontend/.env.example` to `frontend/.env.local` to reach
an API on port 8000. The source backend runs from `backend/` after `uv sync
--locked --extra test`, with `RUNGUARD_DATABASE_URL` set to the local
PostgreSQL URL and a stable `RUNGUARD_AUTH_SECRET`: `uv run alembic upgrade
head`, then `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`. Run
`uv run python -m app.workers.runner` in another terminal with the same
settings. The [backend guide](backend/README.md) explains API behavior.

## Local data recovery

The historical SQLite volume is backed up and imported **offline** before
using its old accounts with PostgreSQL. The exact read-only volume backup and
guarded import steps are in [local recovery](docs/local-recovery.md); the
normal site must remain stopped during that import. After PostgreSQL is in
use, a private dump and a practice restore into a *new* local database are:

```bash
ARCHIVE_PATH=private-backups/runguard-pg.dump make postgres-backup
ARCHIVE_PATH=private-backups/runguard-pg.dump RESTORE_DB=runguard_restore_check make postgres-restore
make postgres-recovery-smoke
```

These Make targets require the configured PostgreSQL service. Keep the
original SQLite volume and stable signing secret; data written after cutover
is absent from that old snapshot.

## Observability and incidents

Add the optional telemetry profile with `make observability-up`; Grafana is
<http://localhost:3001>, Alertmanager is <http://localhost:9093>, and the
local operator inbox is <http://localhost:9094>. Prometheus, Loki, and Tempo
are available through Grafana Explore. `make observability-down` stops the
telemetry services without deleting their data. The isolated alert and
responder drills require `DEMO_MODE=true` and a unique
`DEMO_PROJECT=rg-demo-<name>`; see [observability](docs/observability.md).
The optional responder image installs the policy and CLI from the pinned
[AutoDotAI/incident-response](https://github.com/AutoDotAI/incident-response)
package. Its automatic action is limited to resuming its isolated demo worker;
it never retries a user's failed run. Normal `docker compose up --build`
does not build or install that optional package. To scan open alerts once or
watch them locally, use the [incident-response launcher](incident-response/README.md).

For optional read-only host Codex/Claude analysis of an owned open incident,
see [incident investigation](docs/incident-ai.md). No CLI login or API key is
mounted into a container. The [reviewer guide](docs/reviewer-guide.md) and
[evidence index](docs/reviewer-evidence.md) show verified local checks and
remaining limitations. The [Zoomcamp evidence audit](docs/zoomcamp-evidence.md)
maps the delivered local work to the course criteria and lists deferred work.
