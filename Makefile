# Local RunGuard commands. Public deployment is deferred to issue #34.
.DEFAULT_GOAL := help

UV ?= uv
NPM ?= npm
PYTHON ?= python3
DOCKER ?= docker
OBS_PROJECT ?= runguard
DEMO_PROJECT ?=
DEMO_MODE ?=
INCIDENT_ID ?=
OWNER_USER_ID ?=
INCIDENT_PROVIDER ?= codex
export OBS_PROJECT DEMO_PROJECT DEMO_MODE INCIDENT_ID OWNER_USER_ID INCIDENT_PROVIDER

OBS_COMPOSE = $(DOCKER) compose -p "$$OBS_PROJECT" -f compose.yaml -f compose.observability.yaml --profile observability
STALL_COMPOSE = $(DOCKER) compose -p "$$DEMO_PROJECT" -f compose.yaml -f compose.observability.yaml --profile observability
RESPONDER_COMPOSE = $(DOCKER) compose -p "$$DEMO_PROJECT" -f compose.yaml -f compose.observability.yaml --profile observability --profile responder

.PHONY: help install install-backend install-frontend test test-backend test-frontend typecheck build build-frontend build-images check migrate api worker worker-codex worker-claude worker-litellm frontend compose-config compose-up compose-status compose-logs compose-down compose-smoke postgres-smoke postgres-backup postgres-restore postgres-recovery-smoke observability-up observability-smoke observability-down guard-demo demo-stall-up demo-stall-smoke demo-stall-down responder-up responder-smoke responder-down guard-incident incident-investigate incident-review incident-retry cd-check rollback-rehearsal

help: ## List every Makefile target; no install or service start
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "%-24s %s\n", $$1, $$2}' Makefile

install: install-backend install-frontend ## Install locked backend and frontend dependencies
install-backend: ## Install backend dependencies with uv.lock
	cd backend && $(UV) sync --locked --extra test
install-frontend: ## Install frontend dependencies with package-lock.json
	cd frontend && $(NPM) ci --no-audit --no-fund

test: test-backend test-frontend ## Run offline backend and frontend suites
test-backend: ## Run backend pytest (dependencies must be installed)
	cd backend && $(UV) run --no-sync pytest -q
test-frontend: ## Run frontend Vitest suite
	cd frontend && $(NPM) test
typecheck: ## Type-check frontend TypeScript
	cd frontend && npx tsc --noEmit
build: build-frontend ## Build the frontend application
build-frontend: ## Build frontend with Vite
	cd frontend && $(NPM) run build
build-images: ## Build local backend and web Docker images
	$(DOCKER) build -t runguard-api:local ./backend
	$(DOCKER) build -t runguard-web:local ./frontend
check: install test typecheck build cd-check ## Install and run all local code and CD checks

migrate: ## Apply Alembic migrations to the selected local SQLite database
	cd backend && $(UV) run alembic upgrade head
api: ## Start loopback API in this terminal; export a stable RUNGUARD_AUTH_SECRET first
	@test -n "$$RUNGUARD_AUTH_SECRET" || { echo 'Set stable RUNGUARD_AUTH_SECRET (at least 32 bytes) first' >&2; exit 2; }
	cd backend && $(UV) run uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
worker: ## Start one host worker; Codex CLI is default (same API database URL)
	cd backend && $(UV) run python -m app.workers.runner
worker-codex: ## Start one host worker using signed-in Codex CLI (local subscription)
	cd backend && RUNGUARD_MODEL_PROVIDER=codex $(UV) run python -m app.workers.runner
worker-claude: ## Start one host worker using signed-in Claude CLI
	cd backend && RUNGUARD_MODEL_PROVIDER=claude $(UV) run python -m app.workers.runner
worker-litellm: ## Start one host worker with server-side LLM_MODEL and provider key
	@test -n "$$LLM_MODEL" || { echo 'Set LLM_MODEL and its server-side provider credential first' >&2; exit 2; }
	cd backend && RUNGUARD_MODEL_PROVIDER=litellm $(UV) run python -m app.workers.runner
frontend: ## Start local Vite frontend in this terminal; configure frontend/.env.local
	cd frontend && $(NPM) run dev

compose-config: ## Validate local Compose configuration (.env with stable secret required)
	$(DOCKER) compose config --quiet
compose-up: ## Build/start local API, worker, web and PostgreSQL; retain both volumes
	$(DOCKER) compose up --build -d
compose-status: ## Show local Compose service status
	$(DOCKER) compose -p runguard -f compose.yaml -f compose.observability.yaml --profile observability ps --all
compose-logs: ## Show last 50 API, worker and web log lines
	$(DOCKER) compose logs --tail=50 api worker web
compose-down: ## Stop local Compose stack without deleting SQLite volume
	$(DOCKER) compose -p runguard -f compose.yaml -f compose.observability.yaml --profile observability down
compose-smoke: ## Run API-to-worker smoke (uses example.com outbound HTTPS)
	$(PYTHON) scripts/compose_smoke.py
postgres-smoke: ## Check isolated PostgreSQL health and restart persistence
	$(PYTHON) scripts/postgres_compose_smoke.py
postgres-backup: ## Save private PostgreSQL dump; set ARCHIVE_PATH=private-backups/name.dump
	@test -n "$$ARCHIVE_PATH" || { echo 'Set ARCHIVE_PATH=private-backups/name.dump' >&2; exit 2; }
	bash scripts/local_pg_recovery.sh backup "$$ARCHIVE_PATH"
postgres-restore: ## Restore dump into a NEW database; set ARCHIVE_PATH and RESTORE_DB
	@test -n "$$ARCHIVE_PATH" && test -n "$$RESTORE_DB" || { echo 'Set ARCHIVE_PATH and RESTORE_DB' >&2; exit 2; }
	bash scripts/local_pg_recovery.sh restore "$$ARCHIVE_PATH" "$$RESTORE_DB"
postgres-recovery-smoke: ## Verify isolated pg_dump/pg_restore login and history
	cd backend && $(UV) run --no-sync python ../scripts/postgres_recovery_smoke.py

observability-up: ## Add loopback Grafana/collector to the existing local app
	$(OBS_COMPOSE) up --build -d
observability-smoke: ## Run offline telemetry smoke for OBS_PROJECT
	RUNGUARD_COMPOSE_PROJECT="$$OBS_PROJECT" $(PYTHON) scripts/observability_smoke.py
observability-down: ## Stop telemetry services; keep the local app and volumes running
	$(OBS_COMPOSE) stop collector observability alertmanager operator-inbox

guard-demo: ## Validate DEMO_MODE=true and a dedicated DEMO_PROJECT=rg-demo-<name>
	@test "$$DEMO_MODE" = true || { echo 'Set DEMO_MODE=true for a local demo only' >&2; exit 2; }
	@printf '%s\n' "$$DEMO_PROJECT" | grep -Eq '^rg-demo-[a-z0-9]([a-z0-9-]*[a-z0-9])?$$' || { echo 'Set DEMO_PROJECT=rg-demo-<safe-name>' >&2; exit 2; }
demo-stall-up: guard-demo ## Start isolated paused-worker drill stack (requires .env)
	RUNGUARD_DEMO_MODE=true RUNGUARD_DEMO_PROJECT="$$DEMO_PROJECT" RUNGUARD_ALLOWED_HOSTS=example.com COMPOSE_PROJECT_NAME="$$DEMO_PROJECT" $(STALL_COMPOSE) up --build -d
demo-stall-smoke: guard-demo ## Run gated >60s stalled-worker alert drill
	RUNGUARD_DEMO_MODE=true RUNGUARD_DEMO_PROJECT="$$DEMO_PROJECT" RUNGUARD_ALLOWED_HOSTS=example.com COMPOSE_PROJECT_NAME="$$DEMO_PROJECT" $(PYTHON) scripts/demo_stall_smoke.py
demo-stall-down: guard-demo ## Stop gated drill stack without deleting its volume
	RUNGUARD_DEMO_MODE=true RUNGUARD_DEMO_PROJECT="$$DEMO_PROJECT" RUNGUARD_ALLOWED_HOSTS=example.com COMPOSE_PROJECT_NAME="$$DEMO_PROJECT" $(STALL_COMPOSE) down
responder-up: guard-demo ## Start isolated responder drill stack (requires .env)
	RUNGUARD_DEMO_MODE=true RUNGUARD_DEMO_PROJECT="$$DEMO_PROJECT" RUNGUARD_ALLOWED_HOSTS=example.com COMPOSE_PROJECT_NAME="$$DEMO_PROJECT" $(RESPONDER_COMPOSE) up --build -d
responder-smoke: guard-demo ## Run gated alert-to-responder-to-recovery drill
	RUNGUARD_DEMO_MODE=true RUNGUARD_DEMO_PROJECT="$$DEMO_PROJECT" RUNGUARD_ALLOWED_HOSTS=example.com COMPOSE_PROJECT_NAME="$$DEMO_PROJECT" $(PYTHON) scripts/responder_smoke.py
responder-down: guard-demo ## Stop responder drill stack without deleting its volume
	RUNGUARD_DEMO_MODE=true RUNGUARD_DEMO_PROJECT="$$DEMO_PROJECT" RUNGUARD_ALLOWED_HOSTS=example.com COMPOSE_PROJECT_NAME="$$DEMO_PROJECT" $(RESPONDER_COMPOSE) down

guard-incident: ## Require an existing open INCIDENT_ID and OWNER_USER_ID; provider codex/claude
	@test -n "$$INCIDENT_ID" && test -n "$$OWNER_USER_ID" || { echo 'Set INCIDENT_ID and OWNER_USER_ID for an existing open incident' >&2; exit 2; }
	@case "$$INCIDENT_PROVIDER" in codex|claude) ;; *) echo 'INCIDENT_PROVIDER must be codex or claude' >&2; exit 2;; esac
incident-investigate: guard-incident ## Opt-in read-only host CLI analysis; existing login required
	@cd backend && $(UV) run python -m app.incident_ai investigate "$$INCIDENT_ID" --owner-id "$$OWNER_USER_ID" --provider "$$INCIDENT_PROVIDER"
incident-review: guard-incident ## Review saved advisory for an owned incident; no CLI call
	@cd backend && $(UV) run python -m app.incident_ai review "$$INCIDENT_ID" --owner-id "$$OWNER_USER_ID"
incident-retry: guard-incident ## Explicit new advisory attempt; existing host CLI login required
	@cd backend && $(UV) run python -m app.incident_ai retry "$$INCIDENT_ID" --owner-id "$$OWNER_USER_ID" --provider "$$INCIDENT_PROVIDER"

cd-check: ## Validate local CD workflows and fail-closed paths; no deployment
	sh deploy/test_cd.sh
rollback-rehearsal: ## Rehearse A-to-B-to-A rollback locally; no public host operation
	bash deploy/rehearse_rollback.sh
