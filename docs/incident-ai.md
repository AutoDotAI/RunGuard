# Optional host CLI incident investigation

The deterministic responder remains the default alert action. An operator can
ask a host-installed Codex CLI (default) or Claude CLI to analyze one persisted,
open incident. This is an advisory step; neither CLI can approve a remediation,
retry a job, edit RunGuard, or restart a service. The API, job worker and alert
webhook never start the CLI.

## Use a host-readable local database

The normal Compose stack stores SQLite in a Docker-managed volume. For this
optional host command, start a **new local Compose project** with a directory
you own. Keep `.env` as described in the root README.

```bash
mkdir -p "$PWD/.local-data/host-ai"
export RUNGUARD_HOST_DATA_DIR="$PWD/.local-data/host-ai"
docker compose -p rg-host-ai -f compose.yaml -f compose.host-ai.yaml up --build -d --wait
```

The override binds only the SQLite data directory. It does not bind
`~/.codex`, `~/.claude`, API tokens, or `CODEX_HOME` into containers. Do not
reuse a Compose project that already has a named data volume. `docker compose
down` without `-v` retains the local data. The host command opens the same
SQLite file directly, so keep one API and one worker on the host.

When a real alert has produced a responder incident, use the `incident_id`
and `owner_user_id` from the responder's local output or incident record.
From the repository root:

```bash
export RUNGUARD_DATABASE_URL="sqlite:////${RUNGUARD_HOST_DATA_DIR#/}/runguard.db"
cd backend
uv sync --frozen --extra test
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
