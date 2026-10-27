# RunGuard — instructions for coding agents

This is ONE Git repository for an existing Lovable-generated React/TypeScript frontend plus a FastAPI API and a separately running Python job worker.

## Source of truth
- Approved product scope: [`_docs/old/SPEC.md`](_docs/old/SPEC.md).
- Engineering workflow: [`_docs/process.md`](_docs/process.md).
- Team: [`_docs/team/pm.md`](_docs/team/pm.md), [`_docs/team/software-engineer.md`](_docs/team/software-engineer.md), [`_docs/team/qa-engineer.md`](_docs/team/qa-engineer.md).
- Task tracker: public GitHub Issues.

## Architectural guardrails
1. Before editing, inspect the actual Lovable source, package scripts and authentication adapter. **Do not overwrite, regenerate or relocate existing frontend files without showing why.** Preserve the Lovable source snapshot and its synchronization workflow.
2. FastAPI owns local email/password authentication. Store salted password hashes in the app database, issue signed expiring bearer JWTs, and derive per-user ownership from the verified subject. The frontend must use FastAPI for auth and for workers, runs, and logs; never store plaintext passwords in the browser.
3. The approved target database is PostgreSQL through SQLAlchemy and Alembic; the current SQLite volume is the preserved source for a one-time, verified import. A dedicated Python worker claims durable queued jobs through short transactions. Do not execute user jobs inside FastAPI request handling, and do not substitute Celery, Redis, or LangGraph without explicit approval.
4. Two worker types: `website_monitor` and `url_summarizer`. Fresh accounts default to the clearly labeled local extractive demo, which uses no AI model. Host Codex CLI, host Claude CLI, and server-side LiteLLM API-key access remain optional real-model choices through an authenticated provider/model settings page. Keep CLI login material outside containers and API keys outside browser storage. No automatic job retries; manual retry creates a new run from the original provider/model snapshot.
5. Public URL fetching is a security boundary: SSRF prevention requires more than URL parsing or DNS validation. Keep local URL restrictions and tests effective; if public deployment is later approved, require a restricted public-host allowlist and enforced egress restrictions until destination pinning is proven. Do not advertise arbitrary URL support as secure.
6. API and worker must share one persistent local PostgreSQL service after cutover. Preserve and back up the old SQLite volume until import verification passes. No horizontally scaled replicas. Never commit `.env`, access tokens, databases, generated secrets, or personal page content.
7. Maintain an explicit OpenAPI agreement at [`openapi.yaml`](openapi.yaml), and sync frontend types/client and backend endpoints to it. Follow the actual source structure.
8. Preserve UX: loading/empty/error states, polling (not WebSockets), append-only timestamped logs, and status values `queued/running/succeeded/failed`.
9. This milestone is local-only. Use `compose.yaml` and optional `compose.observability.yaml`; public deployment and CD are deferred. Do not claim a deployed service.

## Change discipline
- One issue or one vertical slice at a time. Implement the smallest correct change; don't perform unrelated refactors.
- For each task: cite relevant spec sections, define acceptance checks, implement tests, execute them, report exact results, and explain any verification gaps.
- Keep implementation and independent QA in separate agent contexts/sessions. QA must not edit production code in its review pass.
- Never claim commands passed or cloud deployments succeeded if not actually executed..
- Pause before destructive git commands, GitHub pushes, paid cloud creation, or exposing services publicly.
- Make small commits with descriptive messages (after reviewing `git diff`).

## Preferred implementation conventions
- Frontend: React/TypeScript, existing Lovable tooling and design system; central API service adapter.
- Backend: Python 3.12+ (if supported), FastAPI, Pydantic, SQLAlchemy, Alembic, pytest. Use `uv` if available, otherwise document the supported install path.
- API prefix: `/api/v1` per spec. Add `GET /health` for non-sensitive readiness.
- Test external HTTP requests and LiteLLM via injected/mocked adapters; CI must not require live API keys.
- Provide actual commands for this repository in `README.md` as soon as they work; don't invent terminal results.
