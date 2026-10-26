# RunGuard task backlog

These are the original proposed work items; public GitHub issues are the live tracker. The approved behavior is in `_docs/old/SPEC.md`. The current backend persists accounts, workers, runs, logs, alerts, and responder audit in SQLite; `frontend/` remains the Lovable app.

**Scope amendment — 2026-10-26:** The owner chose a local-only delivery because no server or domain is available. Issues #23–#25 now accept a local security/egress probe, CI-gated digest publication and host-free CD validation, and an isolated A→B→A rollback rehearsal. External HTTPS, actual-host CD, and rollback proof move to [issue #34](https://github.com/AutoDotAI/RunGuard/issues/34). Model-provider credentials are optional, server-side API keys; an agent/ChatGPT subscription is not an application credential. The original task wording below is preserved as historical planning context; the amended GitHub issue criteria govern completion.

**Provider amendment — 2026-10-26:** The owner requested host-side CLI use of an existing Codex login as the default model path, with Claude CLI selectable and LiteLLM API-key access optional. This is not yet an implemented or verified RunGuard provider; see the approved spec amendment and tasks #35–#36 below. CLI login/session material must remain on the host, outside containers. Website Monitor remains deterministic and the responder's action set remains bounded.

**Current local-only amendment — 2026-10-27:** The owner explicitly requested PostgreSQL, a frontend AI provider/model page, and stronger worker tests, but is **not deploying** RunGuard. Tasks #37 onward supersede historical SQLite and deployment wording above; their numbers are backlog identifiers, not GitHub issue numbers. Public hosting and CD remain deferred under [issue #34](https://github.com/AutoDotAI/RunGuard/issues/34). Preserve the existing SQLite volume and data during migration. The current local target uses `compose.yaml` and optional `compose.observability.yaml`; the tasks below identify cleanup only after functionality has been consolidated. The [Zoomcamp project brief](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/tree/main/project) remains a review checklist, with deployment and CD reported as unfulfilled rather than claimed complete.

## 1. Set up an empty project with a passing test
Goal: Establish a clean-checkout baseline that starts and passes one automated test.
Description: In the current workspace, verify that `backend/` can install its dependencies and run a minimal `/health` test from a fresh environment; keep the existing Lovable source intact. Record the exact install and test commands and make the test pass without external services or credentials.

## 2. Reconcile the repository layout
Goal: Put project instructions and application sources under one reviewable repository layout.
Description: Inspect the outer Git root and the nested Git repository in `frontend/`, then document a safe way to preserve Lovable synchronization and history while tracking `backend/`, `_docs/`, and the frontend together. Do not rewrite published Lovable history; verify frontend build paths after any layout decision.

## 3. Freeze the production API contract
Goal: Make `openapi.yaml` describe the approved P0 API and the actual frontend data types.
Description: Compare `openapi.yaml` with `frontend/src/lib/api/types.ts` and `_docs/old/SPEC.md` §8, including pagination, errors, local register/login, signed bearer authentication, and all run states. Keep the backend-owned auth endpoints in the production contract and add a check that catches missing routes or incompatible schemas.

## 4. Connect frontend local authentication
Goal: Replace browser-stored mock accounts with FastAPI email/password registration and login.
Description: Implement `frontend/src/lib/auth.tsx` against `/api/v1/auth/register` and `/api/v1/auth/login`, store only the returned bearer token and user identity, and clear them on logout. Handle validation, duplicate accounts, invalid credentials, expired tokens, and protected-route redirects; never store plaintext passwords in the browser.

## 5. Persist local accounts and verify bearer tokens
Goal: Authenticate every protected API request using local SQLite accounts and signed, expiring JWTs.
Description: Move the demo users and salted password hashes into the SQLite `users` table, and keep registration/login in FastAPI with normalized unique emails. Verify signature, issuer, audience, expiry, and subject in `backend/app/auth.py`; test bad credentials, duplicate accounts, forged or expired tokens, and ownership from the verified subject.

## 6. Build the persistent SQLite database
Goal: Persist workers, runs, and run logs across API and worker restarts.
Description: Replace `backend/app/store.py`'s process-local dictionaries with SQLAlchemy models and an Alembic migration for the tables and indexes in `_docs/old/SPEC.md` §7. Test migration from an empty database and verify an API restart retains saved data.

## 7. Build the owned worker CRUD backend
Goal: Provide persistent create, list, view, edit, and soft-archive operations for worker configurations.
Description: Implement the worker routes in `backend/app/routers/workers.py` against SQLite, with server-side name, kind, URL, quota, and ownership rules from `_docs/old/SPEC.md` §§4–5. Test 401 for unauthenticated requests, 404 for another user's IDs, immutable kind, and preserved history after archive.

## 8. Add durable run enqueueing
Goal: Return `202` only after a queued run and its configuration snapshot are stored.
Description: Implement `POST /api/v1/workers/{worker_id}/runs` with a database-enforced one-active-run-per-worker rule and a 10-active-run-per-user limit. Test that two simultaneous launch attempts cannot create duplicate active runs and that stopping the worker leaves the run queued.

## 9. Build the separate worker loop
Goal: Claim and process queued SQLite runs in an independent Python process.
Description: Add a worker entry point under `backend/app/workers/` that atomically claims one run in a short transaction, releases the lock before network work, and writes state transitions and logs. Test single-worker concurrency and startup reconciliation of orphaned `running` runs to visible `WORKER_INTERRUPTED` failures.

## 10. Enforce the outbound URL policy
Goal: Prevent server-side requests to unsafe destinations before enabling real URL fetching.
Description: Implement the rules in `_docs/old/SPEC.md` §10.2 for schemes, credentials, ports, local and reserved addresses, redirects, DNS rebinding risk, response limits, and production egress restrictions. Test blocked destinations and redirects with injected HTTP transport; do not claim arbitrary URL support safe without destination pinning or restricted egress.

## 11. Implement the Website Monitor handler
Goal: Execute a bounded HTTP check and persist its real outcome.
Description: Add the `website_monitor` handler under `backend/app/workers/` with a 10-second timeout and success for HTTP 200–399, then write status, response time, error category, and timestamped steps. Use mocked transport to test 200, redirect policy, 404, 503, timeout, and network errors without live outbound calls in CI.

## 12. Implement the URL Summarizer handler
Goal: Fetch readable HTML and save a summary produced through LiteLLM.
Description: Add bounded HTML extraction and an injectable LiteLLM adapter with a fixed prompt and server-controlled model and credentials. Test readable, empty, non-HTML, truncated, and provider-failure cases with fake HTTP and model responses; disable automatic provider retries.

## 13. Persist incremental run logs
Goal: Serve ordered, append-only step logs for active and completed runs.
Description: Write run events to SQLite with increasing log IDs, UTC timestamps, levels, event codes, and sanitized messages. Implement `GET /api/v1/runs/{run_id}/logs?after_id=` and test cursor behavior, user isolation, and persistence after restart.

## 14. Implement manual retry from the original snapshot
Goal: Let a user retry only an owned failed run without changing the old record.
Description: Implement `POST /api/v1/runs/{run_id}/retry` so the new queued run copies the failed run's original URL and settings and records `retry_of_run_id`. Test rejection for active or successful runs, archived workers, foreign IDs, and a second active run.

## 15. Replace demo dashboard calculations
Goal: Return accurate 24-hour counts, success rate, duration, and recent executions from SQLite.
Description: Implement `GET /api/v1/dashboard/summary` using owned persisted runs and the definitions in `_docs/old/SPEC.md` §4.2. Test the empty state, exclusion of active runs from success rate, and the 24-hour boundary; remove seeded sample results from the production path.

## 16. Build the approved frontend screens
Goal: Make the Lovable React app cover every P0 user journey before live data integration.
Description: Check `frontend/src/routes/` against `_docs/old/SPEC.md` §4 and complete any missing login, dashboard, worker form/list/detail, run detail, failure, retry, loading, empty, and error states using the existing design system. Keep the frontend's current structure and Lovable history, and verify the build and route tests with mocked API responses.

## 17. Connect the frontend to the backend
Goal: Display real owned backend data through one typed frontend service adapter.
Description: Replace `frontend/src/lib/api/client.ts`'s mock selection with HTTP calls matching `openapi.yaml` and attach the backend-issued bearer token from the auth adapter. Verify create, edit, archive, run, logs, retry, dashboard, and 401 handling without fabricated production data.

## 18. Cover backend rules with unit tests
Goal: Catch backend validation, ownership, state, and worker-handler errors before integration.
Description: Add focused tests under `backend/tests/unit/` for JWT verification, URL policy, worker validation, queue transitions, retry snapshots, monitor status mapping, HTML extraction, and LiteLLM error handling. Inject fake clock, HTTP transport, and model responses so this suite needs no live accounts or paid calls.

## 19. Cover frontend behavior with component tests
Goal: Verify the main React screens and interactions independently of a running backend.
Description: Add tests under `frontend/src/` for protected-route redirects, form validation, loading and empty views, API failures, status polling, log cursors, and retry visibility using a fake service adapter. Run the frontend's actual test, type-check, and build commands and record their results.

## 20. Cover the full path with integration tests
Goal: Prove authenticated API, SQLite, and separate worker behavior together.
Description: Use temporary SQLite plus fake HTTP and LiteLLM adapters to test registration/login, two-user isolation, enqueue-to-terminal transitions, persisted logs, retry, worker stop/restart, orphan reconciliation, and database persistence. Keep CI independent of live sites, paid providers, and external auth credentials.

## 21. Containerize web, API, and worker
Goal: Start the complete local system with a single Docker Compose command.
Description: Add container definitions and a one-time migration step so web, API, and one worker start together and API/worker share a same-host persistent SQLite volume. Verify `docker compose up --build`, a full run, and data survival after `docker compose restart`.

## 22. Add continuous integration
Goal: Run all P0 checks automatically on pushes and pull requests.
Description: Add a GitHub Actions workflow that installs locked dependencies, runs backend unit and integration tests, frontend tests and type checks, and builds the container images. Verify it passes without live credentials or paid services and document the same local commands.

## 23. Deploy a safe public demo (original plan; public host deferred)
Goal: Make the full frontend, API, and worker journey available over HTTPS.
Description: Deploy one API replica and one worker on the same host with persistent SQLite, a TLS reverse proxy, protected secrets, limited origins, and outbound controls from `_docs/old/SPEC.md` §10.2. Verify signup, both real worker types, failure, logs, and retry from a clean browser before sharing the URL. **Amended local gate:** [#23](https://github.com/AutoDotAI/RunGuard/issues/23) verifies the local security topology and egress probe; actual hosting is [#34](https://github.com/AutoDotAI/RunGuard/issues/34).

## 24. Add a controlled CD promotion path
Goal: Deploy a tested, versioned image without an unreviewed production change.
Description: Configure a development deployment from the main branch and a manual production promotion of an already tested image digest, as described in `_docs/old/SPEC.md` §14. Keep dev/prod configuration and SQLite volumes separate and record the deployed digest and smoke-test outcome. **Amended local gate:** [#24](https://github.com/AutoDotAI/RunGuard/issues/24) verifies digest publication, artifact provenance, promotion validation, and failure paths without SSH; actual host promotion is [#34](https://github.com/AutoDotAI/RunGuard/issues/34).

## 25. Test the rollback path
Goal: Restore the prior known-good application version after a bad deployment.
Description: Record the previous image digest, take a consistent SQLite backup before a risky migration, and document commands to redeploy that image without assuming migrations roll back. Exercise the procedure in a non-production environment and verify API health plus a user-journey smoke test afterward. **Amended local gate:** [#25](https://github.com/AutoDotAI/RunGuard/issues/25) verifies a private A→B→A rehearsal and schema incompatibility denial; actual-host rollback proof is [#34](https://github.com/AutoDotAI/RunGuard/issues/34).

## 26. Instrument the user journey
Goal: Show how enqueue, worker start, completion, and failure affect users.
Description: Emit sanitized structured logs and correlated traces across API and worker, plus run duration, queue wait, completion, and heartbeat metrics; make them viewable in an observability UI. Verify one run can be followed by request/run/trace ID without using user IDs or raw URLs as metric labels or exposing secrets.

## 27. Alert on real user impact
Goal: Raise a visible alert for failed jobs or a queue stalled by an unhealthy worker.
Description: Implement the `_docs/old/SPEC.md` §13 trigger of repeated user run failures in five minutes or a run queued over 60 seconds because the worker is unhealthy, and store an alert linked to the affected run. Distinguish a target site's HTTP failure from a RunGuard platform outage; test open and resolved states and display the alert in the frontend.

## 28. Create a controlled alert trigger
Goal: Reproduce an alert and capture evidence from symptom through recovery.
Description: Add a deterministic fixture or explicitly development-only operator action that produces a failed run or paused-worker queue delay without risking production users. Trigger it in a demo environment and record the run, logs, metric or trace, alert, and recovery result.

## 29. Implement a bounded responder agent
Goal: Wake an incident-response agent only for a real RunGuard alert.
Description: Give the agent fixed read tools for worker health, failed run, recent errors, and queue depth, then require an evidence-linked diagnosis and audited action record. Limit automatic remediation to the demo-only paused-worker `resume_demo_worker` action, require operator approval in production, and verify it never retries a user's failed job automatically.

## 30. Finish reviewer documentation and evidence
Goal: Give a new reviewer exact instructions to run and inspect the delivered system.
Description: Update the root README with verified install, environment, test, run, deployment, rollback, alert-trigger, and limitation steps, plus screenshots and real AI-development evidence. Distinguish implemented P0 from optional P1 work and check every claim against actual commands or artifacts.

## 35. Add optional host CLI incident analysis
Goal: Let the local responder generate a bounded recommendation through a host Codex CLI login, with Claude CLI selectable, without granting new actions.
Description: Add a host-side backend worker adapter that passes only sanitized, fixed inspection evidence to a selected CLI with strict time and output limits and no shell interpolation or container auth mounts. Keep deterministic offline diagnosis as a fallback; test malicious evidence, unavailable CLI, timeout, audit persistence, and unchanged demo-only `resume_demo_worker` permission with fake CLI processes.

## 36. Add host CLI summarizer provider
Goal: Make the host Codex CLI the default local model path for URL Summarizer while preserving Claude CLI and LiteLLM as selectable alternatives.
Description: Run a bounded host-side worker process against extracted, size-limited page text using fixed CLI arguments, a timeout, sanitized output, and only the host's existing CLI login. Keep CLI credentials out of Compose containers; test provider selection, missing login, timeout, malformed output, prompt injection in page text, and optional server-side LiteLLM API-key behavior with fake processes and model adapters, then verify one local result without a paid API call if CLI access is available.

## 37. Freeze the AI settings API contract
Goal: Define the authenticated provider catalog and per-user settings before implementation.
Description: Add `GET /api/v1/ai/providers` and `GET`/`PUT /api/v1/ai/settings` to `openapi.yaml`, including `backend_cli` Codex/Claude and `api` LiteLLM models, availability, validation errors, and bearer authentication. Sync `frontend/src/lib/api/types.ts` and its client interface, then run the OpenAPI drift and frontend type checks; this is the prerequisite for tasks #45–#47.

## 38. Add the local PostgreSQL Compose service
Goal: Start a persistent healthy PostgreSQL database for local API and worker use.
Description: Add PostgreSQL 16+ with a named volume, health check, private network, and loopback-bound host port to `compose.yaml`; keep secrets in ignored local environment files. Verify startup, health, and persistence after restart without switching application storage or deleting the SQLite volume; task #39 creates its schema.

## 39. Create a PostgreSQL Alembic schema
Goal: Migrate an empty PostgreSQL database to the full RunGuard schema.
Description: Adapt `backend/alembic/` migrations and SQLAlchemy models for PostgreSQL, including the one-active-run partial index, foreign keys, and signed-64 `BIGINT`/`BIGSERIAL` `run_logs.id` cursor. Verify upgrade from empty and fresh-schema constraints in PostgreSQL; do not rewrite existing SQLite data or claim a data migration.

## 40. Port account and worker persistence to PostgreSQL
Goal: Keep authentication and owned worker CRUD functional on the new database.
Description: Replace SQLite URL guards, PRAGMAs, and SQLite-specific transactions in `backend/app/db.py`, auth persistence, and worker store operations with PostgreSQL-compatible SQLAlchemy behavior. Using the schema from task #39, test registration, password-hash login, ownership isolation, worker archive, and restart persistence with an isolated PostgreSQL database.

## 41. Port queue transactions and worker exclusivity
Goal: Execute each queued run once through PostgreSQL while preserving its full state history.
Description: Replace `BEGIN IMMEDIATE` and file-lock assumptions in enqueue, claim, completion, and startup reconciliation with short transactions, `FOR UPDATE SKIP LOCKED` or equivalent, and a tested advisory lock or lease for the single local worker. Against PostgreSQL, race two launchers and two worker processes, then prove one active run per worker, no duplicate claim, and visible `WORKER_INTERRUPTED` after a crash; keep HTTP/model calls outside transactions.

## 42. Port alerts, incident audit, and observability queries
Goal: Preserve local alerting and incident response after removing SQLite as the app database.
Description: Convert raw `sqlite3` and `BEGIN IMMEDIATE` use in alerting, demo controls, responder, incident AI, and metric callbacks to bounded PostgreSQL queries or SQLAlchemy sessions. With tasks #39–#41 in place, trigger and resolve an alert and inspect its audit and metrics after a restart; retain the responder's existing action limits.

## 43. Preserve database access and URL egress isolation
Goal: Let the local worker reach PostgreSQL without allowing arbitrary private URL fetches.
Description: Wire container workers to the Compose PostgreSQL service and host CLI workers to its loopback-bound port; keep URL fetching's existing SSRF restrictions independent of database connectivity. Probe a successful database connection plus rejected RFC1918, metadata, and redirect destinations, and verify no CLI login material enters containers; no production firewall or deployment work is in this local task.

## 44. Import existing SQLite data once
Goal: Move local accounts and run history into PostgreSQL without data loss.
Description: Build a fail-closed offline importer that takes a consistent SQLite backup, leaves the original volume untouched, and copies IDs, salted password hashes, workers, snapshots, runs, logs, alerts, and incident records into an empty migrated PostgreSQL schema. Handle `runs.retry_of_run_id` self-references and reset all sequences, including `run_logs.id`; verify counts, foreign keys, owner isolation, login, and old run/log views, and refuse a nonempty or incompatible target.

## 45. Persist and expose per-user AI settings
Goal: Return truthful available providers and save each user's validated choice.
Description: Using task #37's contract and task #39's schema, add a PostgreSQL settings table and authenticated provider catalog/settings routes; derive availability from the active host-worker and server-side LiteLLM configuration, never browser secrets. Test default Codex, disabled/unavailable providers, unsupported models, owner isolation, and restart persistence without calling a live model.

## 46. Add the frontend AI settings page
Goal: Let a signed-in user select an available model path and model in the Lovable app.
Description: Add protected `/settings/ai` navigation and a page using the typed client from task #37 with loading, unavailable, validation, save-success, and error states; offer only catalog-approved choices. Test route protection, provider/model changes, disabled choices, failed save, and absence of API keys or CLI credentials in browser state; preserve the existing frontend layout.

## 47. Snapshot provider and model per run
Goal: Make every URL Summarizer run use its selected provider/model without hidden fallback.
Description: At enqueue, copy task #45's setting into the run snapshot; manual retry must copy the original run snapshot, while Website Monitor ignores it. Validate the model allowlist and pass an explicitly selected model as a fixed `--model` CLI argument to Codex/Claude or to server-side LiteLLM; the catalog's `default` sentinel uses the CLI's native default without a `--model` flag. Test exact fake-process arguments, setting edits after enqueue, disabled route errors, and no automatic retry.

## 48. Test worker failure boundaries and adapters
Goal: Reproduce the user's PDF and provider failures and prove bounded worker behavior.
Description: Add injected HTTP/CLI/LiteLLM tests showing an oversized or unsupported PDF fails before a model call, bounded HTML reaches the selected provider, and an unavailable provider fails with `MODEL_PROVIDER_UNAVAILABLE` before invocation. Cover Codex, Claude, and LiteLLM timeouts, output size limits, malformed responses, subprocess cleanup, prompt-injection text, retry snapshots, and sanitized logs without paid API calls; keep `LLM_ERROR` for attempted provider failures.

## 49. Run PostgreSQL end-to-end integration tests
Goal: Prove the local frontend/API/worker/database journey against real PostgreSQL.
Description: Start an isolated test database from task #38's service or CI equivalent and exercise signup, ownership, both worker types, enqueue-to-terminal logs, retry, alerting, and restart persistence with fake outbound transports and model adapters. Include concurrent enqueue/claim and two-user isolation; record exact commands and results without assuming a live website or paid provider.

## 50. Replace local backup and recovery commands
Goal: Preserve data through the SQLite cutover and later local PostgreSQL recovery.
Description: Document and test a consistent pre-cutover SQLite backup, the importer from task #44, and `pg_dump`/`pg_restore` for the resulting PostgreSQL database. Demonstrate restore into a fresh local database and verify login/history; explain that restoring the old SQLite snapshot after PostgreSQL writes discards those newer writes, without adding public deployment rollback machinery.

## 51. Consolidate local Compose and retire deployment files
Goal: Give the owner one normal local launch and one optional observability launch.
Description: Review the four current files—`compose.yaml`, `compose.observability.yaml`, `compose.host-ai.yaml`, and `compose.prod.yaml`—and move any needed local services from the last two into the first two before retiring the unused overrides and deployment-only scripts/workflows. Check Makefile/README references, run `docker compose config` and a local smoke journey, and retain the SQLite backup plus useful migration/security commands; the demo, CD, responder, and rollback-rehearsal overrides were already removed.

## 52. Update CI and exact local commands
Goal: Verify the PostgreSQL-based project automatically and make it runnable from a clean checkout.
Description: Add a PostgreSQL service to CI for backend integration tests while keeping unit/frontend tests and container builds independent of live credentials; remove public deployment/CD jobs for the local milestone. Update the Makefile and README with tested install, migrate/import, launch/stop, test, observability, and backup commands; publish actual command results and any host CLI prerequisite.

## 53. Audit the Zoomcamp project evidence
Goal: Show which course project requirements the local deliverable actually meets.
Description: Compare the delivered app and evidence with the [Zoomcamp project brief](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/tree/main/project), covering tests, telemetry, alert trigger, responder, architecture, and reviewer instructions. Mark public deployment and CD as deferred under issue #34 and the on-call agent as demo-bounded where applicable; link only verified screenshots, CI runs, and local command outputs.

## 54. Evaluate three other Zoomcamp projects
Goal: Complete the course's required peer reviewing of other participants' work.
Description: Follow the [official peer-review rule](https://github.com/DataTalksClub/ai-dev-tools-zoomcamp/blob/main/project/README.md#peer-reviewing) and course-platform workflow to evaluate three projects submitted by other participants against the rubric. Record the permitted submission evidence and deadlines; reviews received on RunGuard and coding-agent QA do not count toward this obligation.
