# RunGuard

RunGuard is being built in [public issues](https://github.com/AutoDotAI/RunGuard/issues).
This checkout contains a FastAPI backend with SQLite-backed local accounts,
durable workers, runs, and logs, a separate Python worker process, and a source
snapshot of the existing Lovable frontend in `frontend/`. The frontend uses
the backend for authentication and application data. Lovable editor
commits still go
to `AutoDotAI/screenshot-guard-studio`; see
[_docs/lovable-sync.md](_docs/lovable-sync.md) before importing later changes.

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
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
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
use; public hosting and outbound egress controls are tracked in issue #23.

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

CI does not start the containers, call live target websites or model providers,
publish images, or deploy. Its tests inject outbound HTTP and model fakes;
the local Compose smoke above is the separate runtime check.

## Public deployment preparation

The one-host HTTPS topology, exact destination allowlist, worker firewall,
rate limits, and isolated local probe are documented in
[deploy/README.md](deploy/README.md). Public deployment remains gated on a
supplied host/domain, a repeated host egress probe, and independent external
HTTPS QA for [issue #23](https://github.com/AutoDotAI/RunGuard/issues/23).
