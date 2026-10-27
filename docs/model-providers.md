# Local model providers

URL Summarizer runs in a separate worker; Website Monitor never calls a
model. Signed-in users choose an available summary method on the AI settings
page. All four choices are visible after a clean Compose start; a real path
shows setup controls until it can run. The API reports availability from the
host worker or account-owned API credential. A queued run saves the provider
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

## API key from the settings page

Open `/settings/ai`, choose **API key**, then select **OpenAI** or
**Anthropic**. Enter your own key in the masked field and click **Save key**.
Select an approved model, then click **Save AI settings**. No `.env` edit or
Compose restart is needed. You can replace or delete each provider key there.
Only configured status and model names are returned to the page; the key is
sent once in an authenticated request and stored encrypted for your account.
The browser does not store it. The normal Compose worker can use an account
key without receiving a host CLI login. A real provider request may cost
money; the automated tests use fakes and spend nothing.

An unavailable selected provider fails visibly with
`MODEL_PROVIDER_UNAVAILABLE` before any CLI/API request. A failed attempted
model request uses `LLM_ERROR`. Oversized or unsupported PDF input fails
before model invocation. Tests use fake clients and require no paid provider.
