# RunGuard

RunGuard is being built in [public issues](https://github.com/AutoDotAI/RunGuard/issues).
This checkout currently contains a **demo FastAPI backend** with process-local
sample data and a passing health test, plus a source snapshot of the existing
Lovable frontend in `frontend/`. The frontend uses the backend's local
registration and login endpoints; persistent SQLite accounts, worker execution,
and frontend application-data integration are follow-up issues. Lovable editor
commits still go
to `AutoDotAI/screenshot-guard-studio`; see
[_docs/lovable-sync.md](_docs/lovable-sync.md) before importing later changes.

## Frontend baseline

Requirements: Node.js and npm. From a fresh checkout:

```bash
cd frontend
npm install --no-package-lock
npm run build
npm test
npm run dev
```

For local authentication against the backend on port 8000, copy
`frontend/.env.example` to `frontend/.env.local` before `npm run dev`.
`VITE_API_BASE_URL` contains only the API origin, not a secret; omit it when
the frontend and API share one origin. Worker and run data still use the
mock client until Issue #17.

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
