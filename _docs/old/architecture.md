# RunGuard implementation architecture

Read `_docs/old/SPEC.md` first. This document records the current frontend layout.

## Existing Lovable source
The user's Lovable frontend already lives in their GitHub repository. **Keep its current layout initially** (usually `src/`, `package.json`, `vite.config.ts` at repo root). Moving it under `frontend/` before confirming Lovable synchronization and import paths is unnecessary risk. The spec's `frontend/` tree is illustrative, not a release requirement.

## Desired components
- **Web:** existing React/Vite frontend, FastAPI-backed auth adapter for local register/login/logout, and typed API client for application data.
- **API:** `backend/app/main.py`, routers, auth, models, services; immediate HTTP responses, including 202 when a run is queued.
- **Worker:** `backend/app/workers/runner.py` executed in its own process/container, polling and atomically claiming queued SQLite runs.
- **Storage:** SQLite on one local persistent volume shared between API/worker; migrations run exactly once before both start.
- **External calls:** bounded public HTTP(S) fetches; AI summaries via LiteLLM and a server-chosen provider.
- **Local delivery:** Compose services `web`, `api`, `worker`, plus a one-off migration step where needed. A same-origin `/api` proxy is preferable where feasible; use the app's existing Vite config during development.

## Transaction and concurrency rules
Use short SQLite transactions to claim one queued run and commit *before* performing HTTP or model calls. Never hold a SQLite write transaction across a network call. One global worker, one active run per saved configuration, no automatic retries. On process restart reconcile orphaned `running` entries to `failed` with visible evidence, not silent re-execution.

## Contracts and auth
`openapi.yaml` is a reviewable contract generated/reconciled from frontend expectations and the approved spec. The API exposes FastAPI `/openapi.json` too. FastAPI stores salted password hashes in the SQLite `users` table, issues signed expiring JWTs, and verifies them cryptographically; ownership is always taken from the verified `sub`, not a request field. Return 404 when an item belongs to another user.

## Deployment constraint
A public demo must keep SQLite API/worker on the same host and local persistent disk. Do not deploy the worker separately on an ephemeral host. User-provided URLs are SSRF sensitive; apply `_docs/old/SPEC.md` §10.2 before public exposure.
