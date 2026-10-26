# Public deployment gate (issue #23)

The production topology is for **one Linux host with persistent local Docker
volumes**. It has one API, one worker, one migration job, one web process, and
one Caddy HTTPS proxy. The API, worker, SQLite volume, and control ports are
not published. Caddy alone publishes 80 and 443. `appnet` is internal; the
worker shares the `egress_guard` network namespace. The guard fails closed if
it cannot install its IPv4/IPv6 firewall rules. This host must support Docker
`NET_ADMIN` for that isolated namespace. Do not scale the API or worker.

`RUNGUARD_ALLOWED_HOSTS` accepts one to five **exact** public hostnames. Set it
to a small list you trust; subdomains are separate entries. The API checks it
when workers are saved, and the worker checks it again before each fetch. The
fetcher validates all DNS answers, pins a validated address to its socket,
disables redirects and environment proxies, and limits time/response size. The
worker firewall allows only IPv4 TCP 80/443 to public destinations; it blocks
private, loopback, link-local, reserved, and metadata networks, arbitrary
ports, direct DNS, and IPv6. An operator must repeat the egress probe on the
actual host before opening the public proxy.

## Local readiness checks (do not publish ports)

From the repository root, with Docker Engine, Compose, Python 3, and OpenSSL:

```bash
cd backend && uv sync --locked --extra test && uv run pytest -q && cd ..
cd frontend && npm ci --no-audit --no-fund && npm test && npx tsc --noEmit && npm run build && cd ..
sh deploy/probe_local.sh
docker run --rm -v "$PWD/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" \
  -e RUNGUARD_PUBLIC_HOST=demo.example.org caddy:2.10.2 \
  caddy validate --config /etc/caddy/Caddyfile
```

The probe creates a unique temporary Compose project and volume, builds the
production images, starts the migration, API, web, one worker, firewall guard,
and a listening private-address fixture. It confirms the worker cannot reach
that fixture and a loopback fixture, checks the firewall's default-deny policies
and reserved-network rules, verifies API signup/allowlist/monitor execution,
then restarts API and worker and reads back the account, worker, run, and logs.
Its temporary project is removed on
exit. It does **not** demonstrate an externally reachable HTTPS site or a live
model request.

## Deployment on a supplied host

1. Point a controlled DNS hostname to the host. Permit inbound TCP 80/443
   only; do not publish API, worker, SSH management, or telemetry ports to
   arbitrary clients. Keep Docker and the host firewall maintained. Confirm
   the host supports `NET_ADMIN` for the guard. Do not start the public proxy
   until the local egress probe passes **on that host**.
2. Clone the tested AutoDotAI commit to the host. Copy
   `deploy/production.example` to `.env.production`, `chmod 600` the file,
   replace the hostname and allowlist, and set a newly generated stable
   `RUNGUARD_AUTH_SECRET` using `openssl rand -hex 32`. Keep the same secret on
   every restart; store a backup outside Git. Set `RUNGUARD_SUMMARIZER_AVAILABLE`
   to `true` only after a model, provider key, and live result are verified.
   Secrets are server-side Compose environment values, never frontend build
   arguments. `.env.production` is Git-ignored.
3. Run `docker compose --env-file .env.production -f compose.prod.yaml config
   --quiet`, then `sh deploy/probe_local.sh`. Inspect the guard and worker logs
   privately. Confirm that the private fixture is denied even when a worker
   process bypasses application URL validation.
4. Run `docker compose --env-file .env.production -f compose.prod.yaml up
   --build -d`. The one-shot `migrate` must exit 0 before API/worker startup.
   Run `docker compose --env-file .env.production -f compose.prod.yaml ps --all`
   and `docker compose --env-file .env.production -f compose.prod.yaml logs
   --tail=100 migrate api worker egress_guard proxy`. Never print the full
   Compose config; it contains the signing secret.
5. Confirm valid TLS and HTTP-to-HTTPS redirection externally. Check
   `https://<your-host>/health`, signup, an allowed Website Monitor, its
   terminal run and timestamped logs, a controlled failure and manual retry,
   and second-account isolation in a clean browser. Check rate-limit `429`
   and `Retry-After` with controlled requests. Check that non-allowlisted and
   private destinations are rejected without network traffic. If a model key
   was supplied, verify a live URL Summarizer result and absence of credentials
   or page content in logs; otherwise verify the UI marks it unavailable.
6. Restart and redeploy with the same secret and Compose project name. Verify
   login plus account, worker, run, and log persistence. The SQLite volume is
   `runguard-public_runguard_data` under the default project name; find its
   actual local mount with `docker volume inspect
   runguard-public_runguard_data`. Back it up before upgrades. `docker
   compose --env-file .env.production -f compose.prod.yaml down` retains data;
   `down --volumes` destroys it and must not be used during normal operation.

Public QA must independently repeat the HTTPS, browser, secret, and egress
checks and record the actual hostname, commit/image, commands, and results on
issue #23. Until host/domain/access are supplied and that evidence exists,
issue #23 remains open and the repository is only locally ready.
