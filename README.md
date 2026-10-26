# RunGuard

RunGuard is a **local-first** React/FastAPI application. The current verified
delivery is one Docker Compose host with SQLite, a separate worker, local
telemetry and alerting, and a bounded offline demo responder. There is no
public URL or live provider-backed summarizer result. Public-host proof is
deferred to [#34](https://github.com/AutoDotAI/RunGuard/issues/34).

Start with the [local reviewer guide](docs/reviewer-guide.md) for the complete
signup-to-recovery journey and [QA evidence](docs/reviewer-evidence.md) for
issue-by-issue results. The historical spec has a dated local-scope
amendment in [_docs/old/SPEC.md](_docs/old/SPEC.md).

Deployment backup, manual rollback, and local rehearsal commands are in
[docs/deployment.md](docs/deployment.md).
Local metrics, logs, traces, and an offline API-to-worker smoke are in
[docs/observability.md](docs/observability.md).

RunGuard's work and independent QA are recorded in [public issues](https://github.com/AutoDotAI/RunGuard/issues).
This checkout contains a FastAPI backend with SQLite-backed local accounts,
durable workers, runs, and logs, a separate Python worker process, and a source
snapshot of the existing Lovable frontend in `frontend/`. The frontend uses
the backend for authentication and application data. Lovable editor
commits still go
to `AutoDotAI/screenshot-guard-studio`; see
[_docs/lovable-sync.md](_docs/lovable-sync.md) before importing later changes.

## Makefile command index

Run `make` or `make help` to list every target without installing software or
starting a service. The Makefile wraps the commands below; it does not create
or print credentials. Install [uv](https://docs.astral.sh/uv/), Python 3.12+,
Node.js/npm, and `make`. Docker Engine with Compose is needed for container
targets; Python 3 is needed for the optional smoke scripts.

For separate local processes, use `make install`, then export one stable
`RUNGUARD_AUTH_SECRET` of at least 32 bytes and configure
`frontend/.env.local` from its example. Run `make migrate`, `make api`,
`make worker`, and `make frontend` in separate terminals. The host worker
uses an existing Codex CLI login by default for URL Summarizer; select
`make worker-claude` for a signed-in Claude CLI or `make worker-litellm`
after setting `LLM_MODEL` and the provider credential **only on the host**.
`make worker-codex` selects Codex explicitly. Start exactly one worker with
the same SQLite URL as the API; do not run a host worker alongside the
Compose worker for that database. The Compose worker starts with its model
provider disabled, so Website Monitor still works while summarization
fails visibly until an optional API-key provider is configured. See
[docs/model-providers.md](docs/model-providers.md). The API binds to
127.0.0.1:8000; the frontend opens on the local Vite port. `make check` installs
locked dependencies and runs backend/frontend tests, TypeScript, the frontend
build, and local CD validation. Individual targets include `make test-backend`,
`make test-frontend`, `make typecheck`, `make build`, and `make cd-check`.

For the local container stack, configure the ignored root `.env` as shown
below, then run `make compose-config`, `make compose-up`, and
`make compose-status`. `make compose-smoke` exercises the API-to-worker path
and may contact `example.com`; `make compose-logs` shows bounded logs, and
`make compose-down` stops services **without deleting the SQLite volume**.
The optional local telemetry profile uses `make observability-up`,
`make observability-smoke`, and `make observability-down`. It attaches to the
same local Compose project by default: Grafana is at <http://localhost:3001>,
with Prometheus, Loki, and Tempo in Grafana Explore. Alertmanager is at
<http://localhost:9093>, and the operator inbox at <http://localhost:9094>.
The raw telemetry service ports are not separately published. See
[docs/observability.md](docs/observability.md) before running the smoke, which
briefly stops a worker and belongs in an isolated project. Enabling telemetry
may briefly recreate API/worker; wait for API health before using the site.

The alert and responder drills need a new isolated project and an explicit
demo gate. For example, `make demo-stall-up DEMO_MODE=true
DEMO_PROJECT=rg-demo-mytest`, followed by `make demo-stall-smoke` and
`make demo-stall-down` with the same variables. Substitute `responder-*` for
the bounded responder drill. These commands reject a missing or unsafe
project name before changing Compose state. `make rollback-rehearsal` runs
the local A→B→A exercise; it does not roll back a public host. Public deploy
and actual-host rollback remain deferred to [#34](https://github.com/AutoDotAI/RunGuard/issues/34).

For an **optional** read-only investigation of an already open, owned
incident, use `make incident-investigate INCIDENT_ID=<id>
OWNER_USER_ID=<id>` with an existing host Codex CLI login. Add
`INCIDENT_PROVIDER=claude` for Claude. `make incident-review` reads the
saved advisory without another CLI call; `make incident-retry` explicitly
requests a fresh attempt. Supply the same IDs to each command and set the
same `RUNGUARD_DATABASE_URL` as the local API. These commands never run on
alerts automatically or approve remediation. The normal Docker volume is
not readable by this host command without the documented local bind-mount
setup in [docs/incident-ai.md](docs/incident-ai.md). A CLI login is separate
from a LiteLLM API key; neither is copied into Docker or the browser.

## Frontend baseline

Requirements: Node.js and npm. From a fresh checkout:

```bash
cd frontend
npm ci --no-audit --no-fund
npm run build
npm test
npm run dev
```

For local use against the backend on port 8000, copy
`frontend/.env.example` to `frontend/.env.local` before `npm run dev`.
`VITE_API_BASE_URL` contains only the API origin, not a secret; omit it when
the frontend and API share one origin. The default app uses real HTTP data;
new accounts start with no workers, runs, or logs. Keep the API and worker
running together to process queued jobs.

## Backend baseline

Requirements: Python 3.12 or newer and [`uv`](https://docs.astral.sh/uv/).
From a fresh checkout, install dependencies and run the tests without any
`.env` file or external accounts:

```bash
git clone https://github.com/AutoDotAI/RunGuard.git
cd RunGuard/backend
uv sync --locked --extra test
uv run pytest -q
```

Launch the local API from `backend/`:

```bash
export RUNGUARD_AUTH_SECRET="$(openssl rand -hex 32)"
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

In another terminal, start the queue worker from `backend/` with the same
`RUNGUARD_DATABASE_URL` as the API:

```bash
uv run python -m app.workers.runner
```

Keep this secret stable across restarts. Accounts live in `backend/data/runguard.db`
by default; set `RUNGUARD_DATABASE_URL=sqlite:////absolute/path/runguard.db` to
choose another local SQLite file.

In another terminal, verify readiness:

```bash
curl -i http://127.0.0.1:8000/health
```

The expected response is HTTP `200` with `{"status":"ok"}`. See
[backend/README.md](backend/README.md) for API behavior and limitations.

## Local Docker Compose

Requirements: Docker Engine with the Compose plugin, available host ports
`3000` and `8000`, and Python 3 to run the optional smoke script. Run these
commands at the repository root. Generate `.env` once and **keep it** across
restarts; the JWT signing secret must be at least 32 bytes. The file is ignored
by Git.

```bash
python3 -c 'from pathlib import Path; import secrets; Path(".env").write_text("RUNGUARD_AUTH_SECRET=" + secrets.token_hex(32) + "\n")'
docker compose config --quiet
docker compose up --build -d
docker compose ps --all
curl -fsS http://127.0.0.1:8000/health
```

Open <http://localhost:3000/signup>. The backend is reachable at
<http://localhost:8000>; the frontend image is built with that **browser** API
origin. New accounts show an empty dashboard. The `migrate` container must
exit `0` before `api` and `worker` start, and `api` and `web` should be healthy.
The API and exactly one worker share `runguard_data`, a local named SQLite
volume. Both published ports bind to loopback only. This setup is for local
use. Local production-style egress validation is tracked in
[issue #23](https://github.com/AutoDotAI/RunGuard/issues/23); actual public
hosting and external security proof are deferred to
[issue #34](https://github.com/AutoDotAI/RunGuard/issues/34).

For a local end-to-end smoke, run:

```bash
python3 scripts/compose_smoke.py
docker compose logs --tail=50 api worker web
docker compose restart
docker compose ps --all
docker compose down
```

The smoke script creates a fresh account, confirms its empty state, creates a
Website Monitor for `https://example.com/`, enqueues a run, observes the
separate worker reach a terminal state, and reads timestamped logs. It then
stops the worker, confirms a second queued run survives an API restart,
resumes the worker, and verifies login plus saved worker/run/log data after a
full stack restart. It needs outbound DNS and HTTPS access to `example.com`;
the target check may fail in a network-restricted environment, but the queue,
terminal status, and logs must still work. The offline integration tests in
`backend/tests/integration` are the deterministic check without network or
provider credentials.

Verified locally on 2026-10-26 with Docker Compose: migration exited `0`,
`api` and `web` became healthy, `/health` returned `200`, and the web
`/login` route returned `200`. A headless Chromium signup through the
containerized frontend reached a genuine empty dashboard. The smoke script
observed two successful monitor
runs against `example.com`, seven timestamped logs on the first run, a queued
run surviving an API restart, and the same account/worker/run/logs after a
full restart. A second `docker compose up --build -d` completed with the same
data. A Summarizer run without a model ended as `failed` with `LLM_ERROR`.
After `docker compose down`, the `runguard_runguard_data` volume remained.

`docker compose down` retains `runguard_data`. **Only**
`docker compose down --volumes` deletes the local database and accounts. To
restart an existing installation, reuse the same `.env` and run
`docker compose up --build -d`; do not regenerate the signing secret.
`docker compose logs -f api worker web` follows service logs. A missing secret
fails Compose configuration; a short secret fails the migration preflight.

The stack starts without model credentials. To enable URL Summarizer, append
`LLM_MODEL` and the selected LiteLLM provider credential to `.env`, then
recreate the worker with `docker compose up -d --force-recreate worker`. The
worker alone receives those settings; they are never
built into the frontend image. Without a model, a summarizer run returns its
existing `LLM_ERROR` provider-unavailable result.
A ChatGPT or agent subscription is not a backend API credential. The local
alert responder runs offline and needs no model key.
The owner has requested a future **host-side Codex CLI** default with Claude
CLI selectable; see [#35](https://github.com/AutoDotAI/RunGuard/issues/35)
and [#36](https://github.com/AutoDotAI/RunGuard/issues/36). Those providers
are not implemented in this commit. CLI login material must remain on the
host and must not be copied into the Compose containers.

## Continuous integration

The [CI workflow](.github/workflows/ci.yml) runs on pushes to `main` and pull
requests into `main`; its [GitHub Actions status](https://github.com/AutoDotAI/RunGuard/actions/workflows/ci.yml)
shows the three required checks. In a fresh checkout, these are the local
equivalents (from the repository root):

```bash
cd backend
uv sync --locked --extra test
uv run --no-sync pytest -q
cd ../frontend
npm ci --no-audit --no-fund
npm test
npx tsc --noEmit
npm run build
cd ..
docker build -t runguard-api:ci ./backend
docker build -t runguard-web:ci ./frontend
```

The backend install uses the committed `uv.lock`; `--locked` rejects a stale
lockfile. The frontend uses `package-lock.json` with `npm ci`, including in its
Docker build. The existing `bun.lock` remains part of the Lovable source
snapshot. In a **disposable checkout without an existing `.env`**, validate
Compose with a temporary signing secret:

```bash
python3 -c 'from pathlib import Path; import secrets; Path(".env").write_text("RUNGUARD_AUTH_SECRET=" + secrets.token_hex(32) + "\n")'
docker compose config --quiet
rm -f .env
```

CI does not start the app containers or call live target websites or model
providers. On a passing `main` push it publishes API, web, and egress-guard
images to GHCR, records immutable digests in a same-run artifact, and runs
host-free CD validation. The development host job is skipped because no host
or domain is configured; publishing an image is not a deployment. Pull
requests test and build but cannot publish or deploy. The local Compose smoke
above is the separate runtime check.

## Local deployment preparation and deferred public hosting

The one-host HTTPS topology, exact destination allowlist, worker firewall,
rate limits, and isolated local probe are documented in
[deploy/README.md](deploy/README.md). Local production-style security checks
belong to [#23](https://github.com/AutoDotAI/RunGuard/issues/23). Public
deployment remains deferred to [#34](https://github.com/AutoDotAI/RunGuard/issues/34)
until a host, domain, access method, and external HTTPS/egress QA are available.
A model provider key is optional and server-side; an agent or ChatGPT
subscription is not a backend API credential.

## Local alert and responder drill

For an optional read-only analysis of one persisted incident using the host's
Codex or Claude CLI login, see [host CLI incident investigation](docs/incident-ai.md).

The local observability profile records user-journey metrics, logs, traces,
and owned alerts. The bounded responder reads a real persisted alert,
diagnoses it offline through four fixed inspections, and may resume only the
single paused worker in an isolated demo project. It audits its decision and
checks recovery; it never retries a failed user job. See the exact Compose
commands and offline smoke in [docs/observability.md](docs/observability.md).
