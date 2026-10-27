# Local incident response

RunGuard's bounded responder policy lives in the pinned
[AutoDotAI/incident-response](https://github.com/AutoDotAI/incident-response)
package. This folder provides a local operator command. It scans **persisted
open RunGuard alerts** and records incidents and audited action decisions in
PostgreSQL. It does not accept an arbitrary webhook or run Codex against the
source tree.

From a fresh checkout, start the normal site, then use a second terminal:

```bash
docker compose up --build -d
./incident-response/run.sh once
```

The launcher builds the optional responder image before scanning; later builds
reuse Docker cache. Normal Compose startup does not build that image. To keep checking for new open alerts:

```bash
./incident-response/run.sh watch
```

`watch` runs one scan at a time, every 15 seconds by default. Set
`RUNGUARD_RESPONDER_INTERVAL_SECONDS` to an integer from 5 through 300 to
change the interval. Press **Ctrl-C** to stop; `SIGTERM` also stops it. A failed
scan reports an error and is tried again after the interval. Repeated scans
use the external package's persisted incident/action deduplication, so they
do not repeat a recorded action.

The `once` command exits nonzero if the responder image cannot build or a scan
fails. It needs Docker Compose and Bash on the host; no host Python, Node,
`uv`, model key, or CLI login. It uses `compose.yaml` and the existing optional
`compose.observability.yaml` responder profile, with no extra Compose file.
If you run a named Compose project, set the same `COMPOSE_PROJECT_NAME` for
both the site and this command.

Alerts are visible in the app dashboard. The responder writes
`responder_incidents` and `responder_actions` to the same PostgreSQL database.
With the default local database name, inspect them read-only with:

```bash
docker compose exec -T postgres psql -U runguard -d runguard \
  -c 'SELECT id, alert_id, status, created_at FROM responder_incidents ORDER BY created_at DESC LIMIT 10'
docker compose exec -T postgres psql -U runguard -d runguard \
  -c 'SELECT incident_id, action, decision, result FROM responder_actions ORDER BY created_at DESC LIMIT 10'
```

The responder can perform only the existing, audited `resume_demo_worker`
action for a bound stalled fixture in an explicit `rg-demo-*` project. Other
incidents produce a diagnosis and an approval-required or denied decision;
this command does not edit application code or retry user jobs. For an
isolated alert-to-recovery drill, see `make responder-smoke` and
[observability documentation](../docs/observability.md).
