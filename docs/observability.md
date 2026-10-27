# Local user-journey telemetry

RunGuard records request, queue, worker, and result signals locally. The
operator dashboard is for development and single-host testing; it is not a
public Grafana deployment. No telemetry account or model provider key is
needed. The Website Monitor smoke uses injected responses and never fetches
its example URLs.

From the repository root, configure `.env` as described in the root README.
`make compose-up` starts only the site at <http://localhost:3000> and API at
<http://localhost:8000>. Add telemetry to that **same** Compose project with:

```bash
make observability-up
```

Enabling telemetry once may briefly recreate the API and worker to add their
collector settings; wait for `docker compose ps` to show a healthy API before
submitting a form. Run the smoke only on an isolated project with no user jobs:
it briefly stops the worker. Use `make observability-down` to stop telemetry
services while keeping the ordinary site and PostgreSQL volume running. The
isolated offline telemetry smoke uses `OBS_PROJECT=rg-observe-demo make
observability-smoke` only after starting that isolated project on free ports.

Open the dashboard at
<http://localhost:3001/d/runguard-journey/runguard-user-journey> or select
**Prometheus**, **Loki**, and **Tempo** in <http://localhost:3001/explore> for
metrics, logs, and traces. Alertmanager is at <http://localhost:9093> and the
local operator inbox at <http://localhost:9094>. Their raw Prometheus, Loki,
and Tempo ports are not published separately. In remote VS Code, forward ports
3000, 8000, 3001, and optionally 9093/9094 to your browser.
The Grafana development image uses `admin` / `admin` by default; change
`RUNGUARD_GRAFANA_PASSWORD` in the Compose environment if the host is shared.
The UI binds only to `127.0.0.1`; the OTLP collector and backend receiver have
no published host ports. No public proxy is configured.

## User-impact alerts

The API evaluates owned PostgreSQL run state every 10 seconds, even with no browser
open and the worker stopped. Two distinct failed runs in a rolling five-minute
UTC window open a `repeated_run_failure` alert. A queued run older than 60
seconds opens a `worker_queue_stalled` alert only when the worker heartbeat is
older than 15 seconds or absent. The exact five-minute and 60-second boundaries
are tested. A later successful run clears that account's repeated-failure
condition; a stalled alert clears when its run is claimed or leaves the queue.
Resolved records remain visible under **Resolved alerts** on the dashboard.
The alert links to the owned run's timestamped logs and shows its trace ID.
Normal 4xx/5xx responses from a configured monitor target count as job failures,
while the stalled alert identifies worker health. They are not labelled a
RunGuard platform outage.

### Controlled local stalled-worker drill

Issue #28 adds an offline rehearsal for the second alert rule. It runs only in
an isolated `rg-demo-*` Compose project with `RUNGUARD_DEMO_MODE=true`. The
ordinary Compose worker never imports the demo control, and no HTTP control
route exists. The demo worker accepts only its bound fixture run and returns
an injected HTTP 200 response; it never contacts the configured target or a
model provider. The API and worker allow only `example.com` worker URLs in this
override. Use a new Compose project and volume; the pause command refuses to
start while any queued or running jobs already exist there.

Prepare a local `.env` with `RUNGUARD_AUTH_SECRET` of at least 32 bytes, then
run from the repository root:

```bash
export RUNGUARD_DEMO_MODE=true RUNGUARD_DEMO_PROJECT=rg-demo-stall-1 RUNGUARD_ALLOWED_HOSTS=example.com
export COMPOSE_PROJECT_NAME="$RUNGUARD_DEMO_PROJECT"
docker compose -p "$RUNGUARD_DEMO_PROJECT" -f compose.yaml -f compose.observability.yaml --profile observability up --build -d
python3 scripts/demo_stall_smoke.py
docker compose -p "$RUNGUARD_DEMO_PROJECT" -f compose.yaml -f compose.observability.yaml --profile observability down
```

The script registers two fresh local accounts through the real API. It pauses
the demo worker, enqueues one fixture run, then waits for queue age to exceed
60 seconds and heartbeat age to exceed 15 seconds. It verifies the owned app
alert, timestamped `DEMO_WORKER_PAUSED` log, fixed-label queue/heartbeat
metrics, firing operator inbox notification, and denied access from the second
account. It resumes only the bound run, verifies completion, the same alert's
resolution, a resolved inbox event, and a cross-process trace. Output contains
only local demo IDs and timestamps. The script always calls cleanup, including
after a failed assertion; cleanup releases a bound pause for processing. It
does not delete the PostgreSQL volume. For a disposable demo volume only, run
`docker compose ... down -v` after reviewing that project's name and data.
Alertmanager may delay the resolved inbox entry by about 30 seconds.

For a manual rehearsal, use the same Compose prefix and run
`docker compose ... exec -T worker python -m app.demo_worker pause`, then
`bind RUN_ID`, `resume`, and `cleanup`. A bound fixture ID is required before
resume. `cleanup` is idempotent. A normal or public Compose service has no
demo mode and rejects these commands; even in demo mode, any unrelated active
job blocks pause, bind, or resume. The demo dashboard explicitly says
**Local demo · injected worker pause**. This is not a production worker
control. The bounded responder below uses this same gate.

### Bounded local responder

The responder policy and CLI live in the separate public
`AutoDotAI/incident-response` package pinned to a tested commit in the
optional responder image. RunGuard supplies its PostgreSQL/demo-control
adapter. The command scans only persisted open
alerts and creates one PostgreSQL incident per alert. Its four fixed inspections
read worker heartbeat, the owned failed run, recent safe error codes plus the
affected run's fixed lifecycle log IDs, and the owned queue depth. They never
copy log message text. A deterministic policy diagnoses target failures separately
from worker/platform failures. It uses no model key, untrusted page text,
arbitrary tool, shell command, or automatic run retry.

Only a `worker_queue_stalled` alert for the one bound, paused fixture in an
explicit `rg-demo-*` project may invoke `resume_demo_worker`. The responder
reserves an audited action before invoking the existing demo gate, then checks
the run and alert for recovery. Outside the demo it records
`approval_required`; a mismatch or unrelated queued job records denial. A
timeout records `needs_review`, never a false recovery. Incident and action
records survive restart and prevent duplicate remediation.

Use a fresh isolated Compose project. Local database and authentication
credentials are generated automatically when `.env` is absent. From the repo
root, run:

```bash
export RUNGUARD_DEMO_MODE=true RUNGUARD_DEMO_PROJECT=rg-demo-responder-1 RUNGUARD_ALLOWED_HOSTS=example.com
export COMPOSE_PROJECT_NAME="$RUNGUARD_DEMO_PROJECT"
docker compose -p "$RUNGUARD_DEMO_PROJECT" -f compose.yaml -f compose.observability.yaml --profile observability --profile responder up --build -d
python3 scripts/responder_smoke.py
docker compose -p "$RUNGUARD_DEMO_PROJECT" -f compose.yaml -f compose.observability.yaml --profile observability --profile responder down
```

The smoke creates its own account and fixture, waits for the real stalled
alert and local firing notification, launches the external
`incident-response --adapter app.responder_adapter:make_adapter once` CLI, verifies
the incident evidence and one audited resume, and checks terminal success plus
the resolved alert and operator notification. Cleanup releases its own demo
control state. `down` retains the isolated PostgreSQL volume; delete it only after
reviewing its project name and data. This local action is not a public
approval or remediation endpoint.

The API exports `runguard_alerts_open{kind}` with only two fixed kind values.
It sends one aggregate firing or resolved state per kind to the local
Alertmanager, which groups and repeats notifications. Alertmanager's UI is
at <http://127.0.0.1:9093>; the local delivery inbox is at
<http://127.0.0.1:9094>. Only fixed alert kind, severity, service and status
reach the inbox. User IDs, emails, URLs, run IDs, tokens and page contents are
absent from metric labels and operator notifications. Both ports bind to
loopback; do not expose them publicly.

To see an alert and its recovery without live HTTP or model calls, use a
dedicated local Compose project with no other queued jobs. Start the
observability profile above, sign up through the app, then run:

```bash
docker compose -p rg-observe-demo -f compose.yaml -f compose.observability.yaml --profile observability stop worker
docker compose -p rg-observe-demo -f compose.yaml -f compose.observability.yaml --profile observability run --rm --no-deps -it worker python -m app.smoke_alerts
docker compose -p rg-observe-demo -f compose.yaml -f compose.observability.yaml --profile observability start worker
```

Enter the existing account email and password at the prompts; the password is
never printed. The script refuses to run while other jobs are queued or
running. It creates two fake 503 monitor failures, pauses so the app and inbox
can be inspected, then creates one fake success and resolves the alert. It
only writes to the chosen local account and PostgreSQL volume. Do not use this
interactive smoke against a shared or public deployment. Alertmanager groups
notifications, so the resolved inbox entry may take about 30 seconds to appear.

The dashboard leads with failed runs, completion rate, queue wait, and worker
heartbeat age. API operation outcomes and queue depth are below those panels.
The API exports `runguard_runs_enqueued_total{kind}`; the worker exports
`runguard_runs_started_total{kind}`,
`runguard_runs_completed_total{kind,status}`,
`runguard_run_duration_seconds{kind}` and
`runguard_queue_wait_seconds{kind}`. The API reads the database worker heartbeat for
`runguard_worker_heartbeat_age_seconds`; it grows while the worker is
stopped. `runguard_queue_depth{kind}` reads committed PostgreSQL queue state. All
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

Stop only telemetry services without deleting the site or PostgreSQL volume:

```bash
make observability-down
```

Do not use `down -v` when you want to keep accounts and run history. The
smoke briefly stops and restarts the worker, so use a dedicated local Compose
project with no queued jobs. It verifies dashboard provisioning, queued
depth, a successful and failed monitor plus an offline fake-model summary,
counters, Loki logs, and Tempo traces crossing API and worker processes. It
also restarts the API and checks that the growing heartbeat age remains one
stable Prometheus series.
