# Local reviewer guide

This guide describes the tested local system. There is no public RunGuard URL.
Public HTTPS and actual-host CD/rollback are deferred to [#34](https://github.com/AutoDotAI/RunGuard/issues/34).
The frontend remains in `frontend/` for Lovable sync; see
[_docs/lovable-sync.md](../_docs/lovable-sync.md).

## What is delivered

| Scope | Current status | Evidence |
| --- | --- | --- |
| P0: local signup/login, owner-scoped workers, runs and logs | Implemented with FastAPI, JWT and SQLite | [#5](https://github.com/AutoDotAI/RunGuard/issues/5), [#20](https://github.com/AutoDotAI/RunGuard/issues/20) |
| P0: Website Monitor and URL Summarizer worker types | Implemented; offline fake-model summary tested. A live summary needs a server-side provider key | [#11](https://github.com/AutoDotAI/RunGuard/issues/11), [#12](https://github.com/AutoDotAI/RunGuard/issues/12) |
| P0: frontend, API, separate worker, tests and local Compose | Implemented and independently checked | [#17](https://github.com/AutoDotAI/RunGuard/issues/17), [#20](https://github.com/AutoDotAI/RunGuard/issues/20), [#21](https://github.com/AutoDotAI/RunGuard/issues/21) |
| P1: CI, digest publication and host-free CD validation | Implemented; no host job has run | [#22](https://github.com/AutoDotAI/RunGuard/issues/22), [#24](https://github.com/AutoDotAI/RunGuard/issues/24) |
| P1: local security probe and A→B→A rollback | Implemented and rehearsed on disposable local volumes | [#23](https://github.com/AutoDotAI/RunGuard/issues/23), [#25](https://github.com/AutoDotAI/RunGuard/issues/25) |
| P1: telemetry, user-impact alert and controlled trigger | Implemented in loopback-only local services | [#26](https://github.com/AutoDotAI/RunGuard/issues/26), [#27](https://github.com/AutoDotAI/RunGuard/issues/27), [#28](https://github.com/AutoDotAI/RunGuard/issues/28) |
| P1 stretch: bounded responder | Implemented offline and independently checked | [#29 QA](https://github.com/AutoDotAI/RunGuard/issues/29#issuecomment-6428628334) |
| Host CLI model path | Requested, pending implementation: Codex CLI default, Claude CLI selectable; no CLI auth in containers | [#35](https://github.com/AutoDotAI/RunGuard/issues/35), [#36](https://github.com/AutoDotAI/RunGuard/issues/36) |
| Public hosting and live provider-backed summary | Deferred or optional; no public URL or paid call is claimed | [#34](https://github.com/AutoDotAI/RunGuard/issues/34) |

## Install and configure

Requirements: Docker Engine with Compose, Python 3, OpenSSL, Node.js/npm and
`uv` for source tests. The committed `frontend/package-lock.json` and
`backend/uv.lock` are the install inputs. From a fresh clone:

```bash
git clone https://github.com/AutoDotAI/RunGuard.git
cd RunGuard
python3 -c 'from pathlib import Path; import secrets; Path(".env").write_text("RUNGUARD_AUTH_SECRET=" + secrets.token_hex(32) + "\n")'
docker compose config --quiet
docker compose up --build -d
docker compose ps --all
curl -fsS http://127.0.0.1:8000/health
```

The one-shot `migrate` service must exit `0`; `api` and `web` become healthy.
Open <http://127.0.0.1:3000/signup>, create a local account and log in. An
empty dashboard is correct for a fresh account. Keep `.env` and its signing
secret stable across restarts; never commit it or a SQLite database. The API
and one worker share the `runguard_data` volume. `docker compose down` keeps
that volume. `down --volumes` destroys it and is outside normal operation.

To run from source instead, follow [backend/README.md](../backend/README.md):
`cd backend && uv sync --locked --extra test && uv run alembic upgrade head`,
then start `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
--no-access-log` and `uv run python -m app.workers.runner` in separate
terminals with the same SQLite URL and signing secret. The frontend uses
`cd frontend && npm ci --no-audit --no-fund && npm run dev` and the
`frontend/.env.example` browser API origin when API and web are separate.

## Acceptance journey

1. Sign up, then log in in the browser. Create a `website_monitor` worker for
   `https://example.com/` and press **Run Now**. Observe `queued`, `running`,
   then `succeeded` or `failed`; the final result and append-only timestamped
   step logs are on the run detail page. A network-restricted host may make
   this real target check fail; the queue and logs remain inspectable.
2. For a reproducible API→SQLite→worker→restart check, run
   `python3 scripts/compose_smoke.py` from the repository root. It signs up a
   fresh account, checks the empty state, enqueues a monitor, reads logs,
   verifies a queued run survives API restart, and checks persistence after
   full restart. Follow with `docker compose logs --tail=50 api worker web`.
3. To inspect a failed run and manual retry, configure a permitted public URL
   that produces an HTTP error, launch it in the UI, then press **Retry** on
   the failed run. The old failed run remains unchanged; the retry is a new
   run from its saved configuration snapshot. The deterministic offline
   failure/retry case is in `backend/tests/integration` and is run with
   `cd backend && uv run --no-sync pytest -q tests/integration`.
4. Create a `url_summarizer` worker if desired. Without a model configured on
   the server, its run fails visibly with `LLM_ERROR`; an offline fake-model
   success is exercised by `RUNGUARD_COMPOSE_PROJECT=rg-observe-demo python3
   scripts/observability_smoke.py` after starting the observability profile
   below. A real provider-backed summary has **not** been verified.
5. The [controlled alert drill](observability.md#controlled-local-stalled-worker-drill)
   starts a new isolated project, pauses only its fixture worker, waits beyond
   60 seconds, verifies one owned alert and a local firing notification, then
   resumes it and verifies resolution. The
   [bounded responder drill](observability.md#bounded-local-responder) repeats
   the path with one audited `resume_demo_worker` action. These use injected
   monitor responses and no model provider.

The drill commands are in [docs/observability.md](observability.md); they
include `docker compose ... --profile observability up --build -d`,
`python3 scripts/demo_stall_smoke.py`, and
`python3 scripts/responder_smoke.py` with separate `rg-demo-*` project names.
Run them only in their isolated local projects, not against existing user
jobs. Grafana, Alertmanager and the local inbox bind to loopback. The
responder has no public action route and never retries a failed user job.

For source tests and builds, from the repository root:

```bash
cd backend && uv sync --locked --extra test && uv run --no-sync pytest -q && cd ..
cd frontend && npm ci --no-audit --no-fund && npm test && npx tsc --noEmit && npm run build && cd ..
sh deploy/test_cd.sh
sh deploy/probe_local.sh
bash deploy/rehearse_rollback.sh
```

The last two commands create disposable isolated Docker projects; the
rehearsal includes a consistent SQLite backup and tests the previous image
against the current schema before rollback. See [docs/deployment.md](deployment.md)
for backup inspection and host-specific commands, and [deploy/CD.md](../deploy/CD.md)
for digest provenance. Actual-host commands in those runbooks are future
instructions, **not** completed deployment evidence.

Stop the ordinary local stack with `docker compose down`; this retains
accounts, runs and logs. Restart with the same `.env` using
`docker compose up --build -d`, then log in again and inspect history.

## Secrets and screenshot evidence

`RUNGUARD_AUTH_SECRET` is a local signing secret, not a user password. User
passwords are submitted to FastAPI over the local connection, stored only as
salted hashes, and never stored as plaintext in the browser. JWTs are held in
browser session storage. Do not commit `.env`, API keys, JWTs, SQLite files,
run page text or screenshots containing personal data. To enable a live URL
Summarizer, configure `LLM_MODEL` and the chosen LiteLLM provider credential
on the **worker** server side as in [README.md](../README.md); do not place it
in Vite variables. A ChatGPT/agent subscription is not a backend API key.
The requested host-side Codex/Claude CLI path is separate work in #35/#36;
this checkout does not yet invoke either CLI. Its existing host login must
remain outside containers and must never be copied into application images.

No screenshot is committed. **Optional, unverified capture command:** with
Chromium installed locally and the Compose web service running, capture the
public signup screen using:

```bash
mkdir -p /tmp/runguard-review
chromium --headless --no-sandbox --window-size=1440,1000 --screenshot=/tmp/runguard-review/signup.png http://127.0.0.1:3000/signup
```

This environment has no `chromium` executable, so the command and its output
path are provided for a reviewer, not claimed as executed evidence. For the
authenticated dashboard, alert, and responder screens, log in to the local
browser and use its screenshot action; save redacted images under
`/tmp/runguard-review/` as `dashboard.png`, `alert.png`, and `responder.png`.
The responder evidence is primarily its SQLite audit and the
`scripts/responder_smoke.py` output; it has no separate public UI.

The [issue/QA evidence index](reviewer-evidence.md) links real independent
verdicts and CI runs. It makes no claim that this optional visual capture or
a live model-provider run was completed.
