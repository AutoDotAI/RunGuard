# Local model providers

URL Summarizer runs in a separate worker; Website Monitor never calls a
model. Signed-in users choose an available summary method on the AI settings
page. For real models, the API reports availability from the host worker or
server-side LiteLLM configuration. A queued run saves the chosen provider
and model snapshot; later settings changes and manual retry do not silently
switch methods or models. There is no automatic provider fallback or job retry.

## Default local demo — no AI model

A fresh account selects the local extractive demo by default. The normal
Compose worker fetches bounded readable HTML and saves a short copied excerpt;
the result and logs identify it as **local demo — no AI model**. No host CLI,
API key, model download, or paid request is needed. The AI settings page shows
worker availability and refreshes while the worker starts. Existing saved
real-provider choices and queued run snapshots are not changed to the demo.

## Optional host Codex or Claude CLI

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
project uses its existing ignored `.env`. Select Codex or Claude through the
AI settings page when the host worker advertises it. The page's
**Refresh providers** button retrieves the current availability after the
host worker starts; an unsaved model choice is retained. For named models, the
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
