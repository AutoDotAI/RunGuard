# Local reviewer guide

RunGuard is a local-only full-stack project. There is no public URL or
production deployment; those are deferred under
[issue #34](https://github.com/AutoDotAI/RunGuard/issues/34). The Lovable
frontend remains in `frontend/` for sync. The [README](../README.md) has
current startup commands, and the [evidence index](reviewer-evidence.md)
links independent issue checks.

## Setup and checks

From a fresh clone, Docker Compose creates private persistent local
credentials; no `.env` or exported variables are needed. From the repository
root:

```bash
docker compose up --build
```

Open <http://127.0.0.1:3000/signup>. PostgreSQL persists accounts, workers,
runs, logs, alerts and settings; `docker compose down` retains the database
volume. An existing installation's ignored `.env` and imported database retain
their credentials and records. The older SQLite volume is preserved separately;
see [local recovery](local-recovery.md) for any pre-cutover installation.
Never use `down --volumes` on the normal project.

For source tests, use `make install`, `make test`, `make typecheck`,
`make build`, and `make security-check`. `make postgres-smoke` tests
persistence in a disposable Compose project; `make first-run-smoke` tests
fresh boot and a synthetic existing-data upgrade. `make compose-smoke` performs
an API→worker journey with `example.com` and requires outbound HTTPS.
Backend unit and integration tests use fake HTTP and model adapters instead
of live paid services.

## User journey

1. Sign up, create a Website Monitor worker for a public URL, and launch it.
   Inspect queued/running/terminal states and append-only logs in the run
   detail page. Target/network failure still has a visible error and logs.
2. Open the AI settings page. A fresh account defaults to **local demo — no AI
   model**; the page refreshes availability if the worker is still starting.
   All four paths are visible. Codex and Claude show host-worker setup until
   connected. For the API path, choose OpenAI or Anthropic, enter your own key
   in the masked form, select an approved model, and save. The key goes to the
   authenticated API, is encrypted for your account, and is never returned.
3. Create a URL Summarizer worker, launch it with a bounded HTML URL, and
   inspect the short copied excerpt, explicit no-AI label, and step logs.
   An oversized PDF fails before summary generation. Manual Retry creates a
   new run from the original provider/model snapshot.
4. Run the isolated alert and responder drill described in
   [observability](observability.md). Its automatic action is limited to
   resuming one demo worker and never retries a user run.

Grafana is available with `make observability-up` at
<http://127.0.0.1:3001>. Prometheus, Loki, and Tempo appear in Grafana
Explore; Alertmanager is at <http://127.0.0.1:9093>. Stop telemetry with
`make observability-down`. Optional host CLI incident analysis is documented
in [incident-ai.md](incident-ai.md). Public deployment and CD are not
implemented for this milestone.
