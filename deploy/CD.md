# Controlled image promotion (issue #24)

`main` CI tests the backend, frontend, and container builds before publishing
three images to GHCR: API/worker, web, and the worker egress guard. Each image
has a `sha-<commit>` tag and an OCI revision label. The workflow records the
registry's immutable `sha256` digests in the `release-images` Actions artifact.
Before development SSH access, the deployment job downloads that artifact from
the same run and verifies its commit and all three digests against the publish
job outputs. Missing or mismatched evidence stops deployment.
Pull requests run CI but cannot publish or deploy. The web build embeds the
repository variable `CD_ALLOWED_HOSTS` (default `example.com`); use the same
allowed public hosts in development and production when promoting one digest.
Changing the list needs a new tested image. No signing key or model key is a
build argument.

## Configure before enabling host deployment

The public security checks in [README.md](README.md) must pass on the actual
host first. This repository does not provision a host or a domain.

1. Create GitHub environments named `development` and `production`. Configure
   **required reviewers** for `production`. A production environment without
   reviewers fails closed before any SSH deployment. Set `CD_ENV_AUDIT_TOKEN`
   in the production environment to a narrowly scoped token with read access
   to repository environment settings, so the workflow can verify the rule.
   Do not use the repository owner as the sole reviewer if GitHub forbids
   self-approval.
2. For each environment, add `CD_SSH_HOST`, `CD_SSH_USER`, `CD_SSH_KEY`, and
   `CD_SSH_KNOWN_HOSTS` as GitHub environment secrets. Pin the actual SSH host
   key; the workflow uses strict host-key checking. Use different hosts or
   accounts if practical. The SSH account needs Docker/Compose access and a
   GHCR **read-only** login on that host. Registry write access exists only in
   CI's image-publishing job.
3. On each host, create `$HOME/runguard/development` or
   `$HOME/runguard/production`. In that directory place `.env.development` or
   `.env.production` from `deploy/production.example`, with a distinct stable
   `RUNGUARD_AUTH_SECRET`, domain, allowed-host list, and
   `RUNGUARD_SMOKE_EMAIL`/`RUNGUARD_SMOKE_PASSWORD`. Choose a dedicated smoke
   account. The first authenticated smoke registers it if login returns 401;
   subsequent releases log in. Keep the file mode `600` and back up its secret
   outside Git. Do not print it or a rendered Compose config in Actions logs.
4. Run the #34 egress/HTTPS checks **on each host** before creating the
   host-local `.cd-security-approved` marker. The deployment script refuses to
   run without the marker. Ensure ports 80/443 and the domain work before
   enabling automation. API and worker must remain on one host with one
   persistent Docker volume; never add replicas.
5. Set repository variables `CD_ALLOWED_HOSTS` to the shared build-time
   allowlist and `CD_SECURITY_GATE_PASSED=true` only after #34 external QA.
   Set `CD_DEV_ENABLED=true` to start automatic development deployments after
   green `main` CI. Set `CD_PROD_ENABLED=true` only after production host QA.
   Missing/false variables skip host jobs; published images alone do not mean
   a site was deployed.

The workflow copies only tracked Compose/scripts to the selected host. It does
not copy `.env`, SQLite, or other secrets. The host generates a temporary
digest-only override from `deploy/compose_overrides.py`; it removes build
contexts, so deployment pulls exactly the recorded digests. The host script
waits for migrations and service health, then checks HTTPS, the login page,
authenticated worker list, and dashboard. An existing account with the wrong
password or a failed health/smoke check fails the job. Attempts, current
successful release, and the previous successful release are recorded under
`state/` on that host. These records contain commit, environment, time, result,
Actions run ID, and three digests; no credentials. A failed attempt writes
`latest-failure.json`, leaves the prior successful record, and requires an
operator to investigate. The manual rollback command and safe SQLite backup
procedure are in [docs/deployment.md](../docs/deployment.md).

## Promotion and evidence

After a successful development deployment, inspect its
`development-deployment` Actions artifact. The manual **Promote tested
RunGuard release** workflow asks only for the source CI run ID and the three
digests from that artifact. It verifies the run was a successful `main` push,
downloads the passed development evidence, and rejects any mismatched digest.
The production job then waits for GitHub's protected `production` environment
approval. It checks that a required-reviewer rule exists before touching the
host. An approved promotion uses the same digests and creates a
`production-deployment` evidence artifact after smoke passes. No workflow
input can specify a host, command, Compose file, mutable tag, or database.

Inspect the Actions jobs and the two deployment artifacts. The host also has
`state/current.json`, `state/previous.json`, and `state/attempts.jsonl`.
Record the actual URL, health, browser, and egress results on #34 before
calling a public deployment complete.

## Host-free validation

From the repository root, with Python 3, `uv`, OpenSSL, and Docker Compose:

```bash
sh deploy/test_cd.sh
cd backend && uv run --no-sync pytest -q
cd ../frontend && npm test && npx tsc --noEmit && npm run build
```

The CD check parses both workflow YAML files with a pinned PyYAML dependency,
rejects duplicate keys and malformed job structure, and proves malformed
manual promotion YAML and missing SSH secrets fail closed. It also tests digest
and evidence validation, including development artifact consumption, shell
syntax, and a merged Compose configuration with no build contexts. A fake
Docker command and fake authenticated smoke exercise failed migration/health
and failed-smoke reporting without a host or network access. These checks use
placeholder digests and temporary local files; they do not pull, publish,
open ports, or deploy. A
live GitHub Actions run is also needed to verify registry permission and
workflow behavior. External host QA remains pending until host/domain access
is supplied.
