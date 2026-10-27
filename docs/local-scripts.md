# Local helper scripts

The normal website starts with `docker compose up --build`. None of the files
in `scripts/` must be run by a recipient to sign up or try either worker type.
They are operator checks and recovery tools. Run a smoke only in its documented
context: several create disposable Docker projects, while `compose_smoke.py`
acts on the already running local project and adds test records.

| Script | Caller | Purpose |
| --- | --- | --- |
| `compose_smoke.py` | `make compose-smoke` | Optional API → worker and restart check against the running local project. It creates a test account and worker. |
| `demo_stall_smoke.py` | `make demo-stall-smoke`, `docs/observability.md`; imported by `responder_smoke.py` | Exercise the isolated paused-worker alert drill. |
| `ensure_local_postgres_env.py` | `docs/local-recovery.md`; `backend/tests/unit/test_local_postgres_env.py` | Preserve an existing auth secret and prepare a stable local PostgreSQL password for offline recovery. |
| `first_run_smoke.py` | `make first-run-smoke`, container CI | Verify no-`.env` Compose startup, registration, Website Monitor, restart persistence, and safe upgrade in disposable projects. |
| `local_pg_recovery.sh` | `make postgres-backup`, `make postgres-restore` | Back up PostgreSQL and restore into a new local database. |
| `observability_smoke.py` | `make observability-smoke` | Check telemetry and user-impact alerts in the selected Compose project. It creates a test account and briefly stops that project's worker; use a disposable project for a clean run. |
| `postgres_compose_smoke.py` | `make postgres-smoke`, container CI | Check isolated PostgreSQL health and persistence across restart. |
| `postgres_recovery_smoke.py` | `make postgres-recovery-smoke` | Check disposable dump/restore plus historical login and run/log reads. |
| `responder_smoke.py` | `make responder-smoke`, `docs/observability.md` | Create an isolated alert fixture and verify the optional responder package records one bounded audited recovery. It is an integration caller, not the responder policy. |

`postgres_network_smoke.py` was removed because it had no caller. Its checks
remain covered by the no-`.env` first-run smoke, the isolated PostgreSQL smoke,
the host-worker loopback URL tests, and the URL security tests (`make
security-check`).
