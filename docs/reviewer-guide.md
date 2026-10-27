# Local reviewer guide

RunGuard is a local-only full-stack project. There is no public URL or
production deployment; those are deferred under
[issue #34](https://github.com/AutoDotAI/RunGuard/issues/34). The Lovable
frontend remains in `frontend/` for sync. The [README](../README.md) has
current startup commands, and the [evidence index](reviewer-evidence.md)
links independent issue checks.

## Setup and checks

Create an ignored root `.env` with a stable `RUNGUARD_AUTH_SECRET` of at
least 32 bytes and `RUNGUARD_POSTGRES_PASSWORD`. Reuse it on restart. From
the repository root:

```bash
docker compose config --quiet
docker compose up --build -d
docker compose ps --all
curl -fsS http://127.0.0.1:8000/health
```

Open <http://127.0.0.1:3000/signup>. PostgreSQL persists accounts, workers,
runs, logs, alerts and settings; `docker compose down` retains the database
volume. The older SQLite volume is preserved separately and needs a verified
offline import before historical records appear. See [local recovery](local-recovery.md).
Never use `down --volumes` on the normal project.

For source tests, use `make install`, `make test`, `make typecheck`,
`make build`, and `make security-check`. `make postgres-smoke` tests
persistence in a disposable Compose project. `make compose-smoke` performs
an API→worker journey with `example.com` and requires outbound HTTPS.
Backend unit and integration tests use fake HTTP and model adapters instead
of live paid services.

## User journey

1. Sign up, create a Website Monitor worker for a public URL, and launch it.
   Inspect queued/running/terminal states and append-only logs in the run
   detail page. Target/network failure still has a visible error and logs.
2. Open the AI settings page to inspect available host Codex, host Claude,
   and server-side API-key models. Only configured and currently available
   paths can be saved. Credentials are never entered in the browser.
3. Create a URL Summarizer worker, launch it with a bounded HTML URL, and
   inspect the result or sanitized failure. An oversized PDF fails before a
   model call. Manual Retry creates a new run from the original snapshot.
4. Run the isolated alert and responder drill described in
   [observability](observability.md). Its automatic action is limited to
   resuming one demo worker and never retries a user run.

Grafana is available with `make observability-up` at
<http://127.0.0.1:3001>. Prometheus, Loki, and Tempo appear in Grafana
Explore; Alertmanager is at <http://127.0.0.1:9093>. Stop telemetry with
`make observability-down`. Optional host CLI incident analysis is documented
in [incident-ai.md](incident-ai.md). Public deployment and CD are not
implemented for this milestone.
