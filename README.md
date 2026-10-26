# RunGuard

RunGuard is being built in [public issues](https://github.com/AutoDotAI/RunGuard/issues).
This checkout currently contains a **demo FastAPI backend** with process-local
sample data and a passing health test. The production Supabase, SQLite, worker,
and Lovable frontend integration are follow-up issues. The existing Lovable
frontend is preserved separately while the repository layout is reconciled in
[issue #2](https://github.com/AutoDotAI/RunGuard/issues/2).

## Backend baseline

Requirements: Python 3.12 or newer and [`uv`](https://docs.astral.sh/uv/).
From a fresh checkout, install dependencies and run the tests without any
`.env` file or external accounts:

```bash
git clone https://github.com/AutoDotAI/RunGuard.git
cd RunGuard/backend
uv sync --extra test
uv run pytest -q
```

Launch the local API from `backend/`:

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal, verify readiness:

```bash
curl -i http://127.0.0.1:8000/health
```

The expected response is HTTP `200` with `{"status":"ok"}`. See
[backend/README.md](backend/README.md) for demo API behavior and limitations.
