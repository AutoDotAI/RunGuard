# Local model providers

URL Summarizer runs in a separate worker; Website Monitor never calls a
model. Signed-in users choose an available provider path and model on the AI
settings page. The API reports only models for which the selected host worker
or server-side LiteLLM configuration is ready. A queued run saves the chosen
provider/model snapshot; later settings changes and manual retry do not
silently switch models. There is no automatic provider fallback or job retry.

## Host Codex or Claude CLI (default path)

Install and sign in to Codex CLI or Claude CLI on the machine running the
worker. Keep CLI login material outside Docker and Git. The local Compose
PostgreSQL service binds its host port to `127.0.0.1`. After starting the site
with `docker compose up --build`, run one host worker from another terminal:

```bash
make install-backend
make worker-codex
# or: make worker-claude
```

The Make target stops the container worker, reads the private Compose database
credential without printing it, and launches exactly one host worker. It works
whether a fresh project generated credentials in its named volume or an older
project uses its existing ignored `.env`. Codex is the default. Claude is selectable through the
AI settings page when the host worker advertises it. For named models, the
worker passes only a backend-allowlisted fixed `--model` argument; the
catalog's `default` value uses the CLI's native model. CLI calls use a
restricted working directory, a bounded timeout and output size, and
sanitized errors. A ChatGPT/Claude subscription login is **not** a LiteLLM
API key.

## Optional server-side API key

Set a backend-approved `LLM_MODEL` list and matching provider credential in
the ignored server environment. The API and worker receive these variables;
the browser bundle and user settings response do not contain the key. Use the
AI settings page to choose the available LiteLLM model. The container worker
starts with its model provider disabled until an API-key route is configured;
it has no Codex/Claude login files or CLI executable. Recreate API and worker
after changing server model configuration:

```bash
docker compose up -d --force-recreate api worker
```

An unavailable selected provider fails visibly with
`MODEL_PROVIDER_UNAVAILABLE` before any CLI/API request. A failed attempted
model request uses `LLM_ERROR`. Oversized or unsupported PDF input fails
before model invocation. Tests use fake clients and require no paid provider.
