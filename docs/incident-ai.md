# Optional host CLI incident investigation

The deterministic responder remains the default alert action. An operator can
ask a host-installed Codex CLI (default) or Claude CLI to analyze one persisted,
open incident. This is an advisory step; neither CLI can approve a remediation,
retry a job, edit RunGuard, or restart a service. The API, job worker and alert
webhook never start the CLI.

## Connect the host CLI to local PostgreSQL

The normal Compose PostgreSQL service binds port 5432 to host loopback. Use
its configured database name, user, password, and port to set the same
`RUNGUARD_DATABASE_URL` for the host command. Stop the container job worker
before starting a host worker against that database. The host's `codex` or
`claude` login stays on the host; no login directory is mounted into Compose.

```bash
export RUNGUARD_DATABASE_URL='postgresql+psycopg://runguard:<password>@127.0.0.1:5432/runguard'
cd backend
uv sync --locked --extra test
```

Substitute the password and optional port from the ignored root `.env`.
Keep this URL out of source control and shell transcripts.

When a real alert has produced a responder incident, use the `incident_id`
and `owner_user_id` from the responder's local output or incident record.
From the repository root:

```bash
uv run python -m app.incident_ai investigate INCIDENT_ID --owner-id OWNER_USER_ID
uv run python -m app.incident_ai review INCIDENT_ID --owner-id OWNER_USER_ID
```

`investigate` uses the existing host `codex` CLI login and invokes it with a
read-only sandbox in an isolated temporary directory. Select Claude with
`--provider claude`. After human review, an explicit `retry` command creates
another audited attempt:

```bash
uv run python -m app.incident_ai investigate INCIDENT_ID --owner-id OWNER_USER_ID --provider claude
uv run python -m app.incident_ai retry INCIDENT_ID --owner-id OWNER_USER_ID
```

Stop an active command with Ctrl-C. The CLI receives only incident, alert,
run, trace and bounded log IDs; fixed event codes; queue depth; worker health;
and a fixed cause code. It receives no user email, target URL or query,
password, token, log message, page content, or arbitrary alert text. Its
response is size-checked and stored as an advisory with a digest. Only an
existing open, owned incident may be analyzed. Repeating `investigate` returns
the prior analysis; `retry` is explicit. Missing CLI/login, timeout, nonzero
exit, or unsafe output records a sanitized fallback without changing the
deterministic incident or any user job. No real CLI is called by CI.

This host CLI login is separate from the optional server-side LiteLLM API key
for URL Summarizer. A subscription login is not copied into RunGuard's API or
worker containers as a provider credential.

## Optional real CLI smoke

This command spends one request through the selected host CLI. Run it only
with an already open incident in the local host-readable stack. Set
and export `INCIDENT_ID`, `OWNER_USER_ID`, and `RUN_ID` from that incident. From `backend/`:

```bash
before_tree="$(git -C .. status --porcelain)"
before_run="$(uv run python -c 'import os; from app.db import UserRepository, RunRecord; u=UserRepository(); s=u.sessions(); print(s.get(RunRecord, os.environ["RUN_ID"]).status); s.close(); u.close()')"
uv run python -m app.incident_ai investigate "$INCIDENT_ID" --owner-id "$OWNER_USER_ID"
uv run python -m app.incident_ai review "$INCIDENT_ID" --owner-id "$OWNER_USER_ID"
after_run="$(uv run python -c 'import os; from app.db import UserRepository, RunRecord; u=UserRepository(); s=u.sessions(); print(s.get(RunRecord, os.environ["RUN_ID"]).status); s.close(); u.close()')"
test "$before_tree" = "$(git -C .. status --porcelain)"
test "$before_run" = "$after_run"
```

`review` should show a completed or sanitized fallback audit with
`human_review_required: true`. The final two checks ensure the repository
and the bound run did not change during analysis. CI uses fake subprocesses
and never runs this smoke.
