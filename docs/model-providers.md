# Local model providers

URL Summarizer runs in the separate Python worker. Website Monitor never calls a model. The host worker uses the existing Codex CLI login by default; set `RUNGUARD_MODEL_PROVIDER=claude` to use the host Claude CLI. `RUNGUARD_MODEL_PROVIDER=litellm` keeps the API-key route through LiteLLM. The selected provider does not fall back to another provider after an error.

## Host subscription path (default)

Install and sign in to Codex CLI or Claude CLI **on the machine running the worker**. Do not put CLI auth files in this repository or mount them into Docker. From a clean checkout:

```bash
cd backend
uv sync --frozen --extra test
export RUNGUARD_DATABASE_URL="sqlite:////absolute/path/to/runguard.db"
export RUNGUARD_AUTH_SECRET="$(openssl rand -hex 32)"
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

In another terminal, set the **same** database URL and signing secret and start exactly one worker:

```bash
cd backend
export RUNGUARD_DATABASE_URL="sqlite:////absolute/path/to/runguard.db"
export RUNGUARD_AUTH_SECRET="<same value used by the API>"
uv run python -m app.workers.runner
```

The default `RUNGUARD_MODEL_PROVIDER=codex` is implicit. For Claude, set `RUNGUARD_MODEL_PROVIDER=claude` before starting the worker. Both adapters pass bounded extracted page evidence on stdin to a fixed CLI command in a temporary directory, with read-only/restricted tool permissions, a 50-second deadline, and a 32-KiB output cap. CLI stderr and subprocess errors never become API errors or run logs. A nonzero exit, timeout, or empty answer fails that run with `LLM_ERROR` and no automatic retry.

The CLI adapters use the operator's existing host login. A browser user never submits a provider credential. A ChatGPT or Claude subscription is not a LiteLLM API key.

## Optional API-key path

Set `RUNGUARD_MODEL_PROVIDER=litellm`, `LLM_MODEL` (for example `openai/gpt-4o-mini`), and the provider's credential in the worker environment. The provider key stays server-side. LiteLLM sends one completion request with retries disabled and a 50-second timeout. CI injects fake clients; it does not need an API key or CLI login.

For the local Compose stack, the container worker starts with `RUNGUARD_MODEL_PROVIDER=disabled`. It can process Website Monitor jobs, while a URL Summarizer run fails visibly with `LLM_ERROR`. To opt into the API-key path, put the LiteLLM settings in `.env` and recreate only the worker:

```bash
docker compose up -d --force-recreate worker
```

Do not set the container provider to `codex` or `claude`: the container has neither the host CLI login nor the CLI executable. For subscription-backed summarization, run the API and worker on the host as shown above. Do not run a host worker concurrently with the Compose worker against the same SQLite queue.

## Verification

Run `cd backend && uv run --no-sync pytest -q tests/test_host_model.py` for fake-CLI selection, timeout, output-limit, and sanitization checks. A live local CLI smoke is optional and uses the already signed-in host account; CI never invokes it.
