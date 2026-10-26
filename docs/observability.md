# Local user-journey telemetry

RunGuard records request, queue, worker, and result signals locally. The
operator dashboard is for development and single-host testing; it is not a
public Grafana deployment. No telemetry account or model provider key is
needed. The Website Monitor smoke uses injected responses and never fetches
its example URLs.

From the repository root, configure `.env` as described in the root README,
then start an isolated local stack on free ports 3000, 8000, and 3001:

```bash
docker compose -p rg-observe-demo -f compose.yaml -f compose.observability.yaml --profile observability up --build -d
docker compose -p rg-observe-demo -f compose.yaml -f compose.observability.yaml --profile observability ps --all
RUNGUARD_COMPOSE_PROJECT=rg-observe-demo python3 scripts/observability_smoke.py
```

Open <http://127.0.0.1:3001/d/runguard-journey/runguard-user-journey>.
The Grafana development image uses `admin` / `admin` by default; change
`RUNGUARD_GRAFANA_PASSWORD` in the Compose environment if the host is shared.
The UI binds only to `127.0.0.1`; the OTLP collector and backend receiver have
no published host ports. The public Caddy profile has no route to them.

The dashboard leads with failed runs, completion rate, queue wait, and worker
heartbeat age. API operation outcomes and queue depth are below those panels.
The API exports `runguard_runs_enqueued_total{kind}`; the worker exports
`runguard_runs_started_total{kind}`,
`runguard_runs_completed_total{kind,status}`,
`runguard_run_duration_seconds{kind}` and
`runguard_queue_wait_seconds{kind}`. The API watches the shared worker lock
file for `runguard_worker_heartbeat_age_seconds`; it grows while the worker is
stopped. `runguard_queue_depth{kind}` reads committed SQLite queue state. All
Prometheus labels come from fixed allowlists. Histograms use seconds.
API and worker resource instance IDs are fixed to their single-host service
names, so a process restart updates the same heartbeat series.

To find a run, copy its ID from the app's run-detail page. In Grafana Explore,
choose Loki and run:

```logql
{service_name="runguard-worker"} | run_id="RUN_ID"
```

Open any returned log's metadata and copy `trace_id`. In Explore, choose Tempo
and use Trace ID lookup to see the API enqueue, worker claim, handler and
result spans together. An enqueue response contains `X-Request-ID`; API error
bodies contain the same `request_id` as that header. Search an API request in
Loki with:

```logql
{service_name="runguard-api"} | request_id="REQUEST_ID"
```

Prometheus queries such as
`runguard_runs_completed_total{status="failed"}` and
`runguard_worker_heartbeat_age_seconds` show user impact directly. The
collector configuration is in `observability/collector.yaml`; the provisioned
dashboard is in `observability/dashboards/runguard.json`.

Operational logs contain only fixed event names, timestamps, service,
severity, and request/run/trace IDs. They do not include user emails, tokens,
passwords, URL query strings, page bodies, model prompts or API keys. The
existing authenticated run-log API remains separate from operator telemetry.

Stop the profile without deleting the application SQLite volume:

```bash
docker compose -p rg-observe-demo -f compose.yaml -f compose.observability.yaml --profile observability down
```

Do not use `down -v` when you want to keep accounts and run history. The
smoke briefly stops and restarts the worker, so use a dedicated local Compose
project with no queued jobs. It verifies dashboard provisioning, queued
depth, a successful and failed monitor plus an offline fake-model summary,
counters, Loki logs, and Tempo traces crossing API and worker processes. It
also restarts the API and checks that the growing heartbeat age remains one
stable Prometheus series.
