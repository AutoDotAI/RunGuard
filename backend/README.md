# RunGuard demo API

This is a process-local backend for the current OpenAPI contract. Registering a
user seeds two sample workers and three clearly labelled sample runs. Passwords
are stored as salted PBKDF2 hashes; registration and login return an eight-hour
HMAC-signed JWT. The signing key is random on startup unless
`RUNGUARD_AUTH_SECRET` is set. All users, runs, and tokens are lost or invalidated
on restart. Queued runs remain queued because this demo has no execution worker.
The sample summaries and logs are examples, not real HTTP or model results.

```bash
cd backend
uv sync --extra test
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal, register a local account and use the returned
`access_token` as `Authorization: Bearer <token>` on `/api/v1/*` routes:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/auth/register \
  -H 'content-type: application/json' \
  -d '{"email":"demo@example.com","password":"DemoPass123!"}'
```

Run tests with `cd backend && uv run pytest -q`. API documentation is at
`http://127.0.0.1:8000/docs`. The Lovable frontend still selects its local mock
client; it has not yet been wired to this API. This backend is for local demo
work and does not satisfy the approved persistent SQLite accounts/data, worker,
or deployment security gates described in the project backlog issues. See the repository-root
`openapi.yaml` for this demo API's current contract.
