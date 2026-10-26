"""Process-local demo state. Nothing in this module survives a restart."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from threading import RLock
from urllib.parse import urlsplit
from uuid import uuid4

from .models import (
    DashboardSummary, EnqueuedRun, Run, RunLog, RunLogPage, RunPage,
    Worker, WorkerCreate, WorkerPage, WorkerUpdate,
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Problem(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def validate_public_url(value: str) -> str:
    """Reject obvious unsafe destinations at save time; no outbound fetch is done here."""
    try:
        parsed = urlsplit(value.strip())
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise Problem(422, "VALIDATION_ERROR", "Invalid URL") from None
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        raise Problem(422, "VALIDATION_ERROR", "A public HTTP(S) URL without credentials is required")
    if port not in {None, 80, 443}:
        raise Problem(422, "VALIDATION_ERROR", "URL port is not allowed")
    host = host.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")) or "." not in host:
        raise Problem(422, "VALIDATION_ERROR", "Local hostnames are not allowed")
    try:
        ip_address(host)
    except ValueError:
        pass
    else:
        raise Problem(422, "VALIDATION_ERROR", "IP address URLs are not allowed")
    return value.strip()


class MemoryStore:
    def __init__(self):
        self.lock = RLock()
        self.users: dict[str, dict] = {}
        self.email_to_id: dict[str, str] = {}
        self.workers: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self.logs: list[dict] = []
        self.next_log_id = 1

    def create_user(self, email: str, password_hash: str) -> dict:
        with self.lock:
            if email in self.email_to_id:
                raise Problem(409, "ACCOUNT_EXISTS", "An account with this email already exists")
            user = {"id": str(uuid4()), "email": email, "password_hash": password_hash}
            self.users[user["id"]] = user
            self.email_to_id[email] = user["id"]
            self._seed_user(user["id"])
            return deepcopy(user)

    def user_by_email(self, email: str) -> dict | None:
        with self.lock:
            user_id = self.email_to_id.get(email)
            return deepcopy(self.users[user_id]) if user_id else None

    def user_by_id(self, user_id: str) -> dict | None:
        with self.lock:
            return deepcopy(self.users.get(user_id))

    def _append_log(self, owner: str, run_id: str, event: str, message: str, level: str = "INFO", timestamp: str | None = None):
        self.logs.append({"id": self.next_log_id, "owner": owner, "run_id": run_id,
                          "timestamp": timestamp or now(), "level": level,
                          "event": event, "message": message})
        self.next_log_id += 1

    def _worker(self, owner: str, worker_id: str, active: bool = True) -> dict:
        worker = self.workers.get(worker_id)
        if worker is None or worker["owner"] != owner:
            raise Problem(404, "NOT_FOUND", "Worker not found")
        if active and worker["archived"]:
            raise Problem(404, "NOT_FOUND", "Worker not found")
        return worker

    def _run(self, owner: str, run_id: str) -> dict:
        run = self.runs.get(run_id)
        if run is None or run["owner"] != owner:
            raise Problem(404, "NOT_FOUND", "Run not found")
        return run

    def _public_worker(self, worker: dict) -> Worker:
        runs = [r for r in self.runs.values() if r["worker_id"] == worker["id"]]
        latest = max(runs, key=lambda r: r["created_at"]) if runs else None
        return Worker(**{k: worker[k] for k in ("id", "name", "kind", "url", "created_at", "updated_at")},
                      last_run={k: latest[k] for k in ("id", "status", "created_at")} if latest else None)

    @staticmethod
    def _public_run(run: dict) -> Run:
        return Run(**{k: v for k, v in run.items() if k != "owner"})

    def list_workers(self, owner: str, limit: int, offset: int) -> WorkerPage:
        with self.lock:
            items = sorted((w for w in self.workers.values() if w["owner"] == owner and not w["archived"]),
                           key=lambda w: w["created_at"], reverse=True)
            return WorkerPage(items=[self._public_worker(w) for w in items[offset:offset + limit]],
                              total=len(items), limit=limit, offset=offset)

    def get_worker(self, owner: str, worker_id: str) -> Worker:
        with self.lock:
            return self._public_worker(self._worker(owner, worker_id))

    def create_worker(self, owner: str, payload: WorkerCreate) -> Worker:
        with self.lock:
            if sum(w["owner"] == owner and not w["archived"] for w in self.workers.values()) >= 20:
                raise Problem(429, "WORKER_LIMIT_REACHED", "You can save at most 20 workers")
            timestamp = now()
            worker = {"id": str(uuid4()), "owner": owner, "archived": False,
                      "name": payload.name, "kind": payload.kind,
                      "url": validate_public_url(payload.url),
                      "created_at": timestamp, "updated_at": timestamp}
            self.workers[worker["id"]] = worker
            return self._public_worker(worker)

    def update_worker(self, owner: str, worker_id: str, payload: WorkerUpdate) -> Worker:
        if not payload.model_fields_set:
            raise Problem(422, "VALIDATION_ERROR", "At least one field is required")
        if any(getattr(payload, field) is None for field in payload.model_fields_set):
            raise Problem(422, "VALIDATION_ERROR", "Fields cannot be null")
        with self.lock:
            worker = self._worker(owner, worker_id)
            if payload.name is not None:
                worker["name"] = payload.name
            if payload.url is not None:
                worker["url"] = validate_public_url(payload.url)
            worker["updated_at"] = now()
            return self._public_worker(worker)

    def archive_worker(self, owner: str, worker_id: str) -> None:
        with self.lock:
            self._worker(owner, worker_id)["archived"] = True

    def _enqueue(self, owner: str, worker: dict, url: str, retry_of: str | None, name: str) -> EnqueuedRun:
        active = [r for r in self.runs.values() if r["owner"] == owner and r["status"] in ("queued", "running")]
        existing = next((r for r in active if r["worker_id"] == worker["id"]), None)
        if existing:
            raise Problem(409, "RUN_ALREADY_ACTIVE", "This worker already has an active run", {"run_id": existing["id"]})
        if len(active) >= 10:
            raise Problem(429, "TOO_MANY_ACTIVE_RUNS", "At most 10 runs may be active")
        run = {"id": str(uuid4()), "owner": owner, "worker_id": worker["id"],
               "worker_name": name, "kind": worker["kind"], "status": "queued", "url": url,
               "created_at": now(), "started_at": None, "finished_at": None,
               "duration_ms": None, "result": None, "error": None, "retry_of_run_id": retry_of}
        self.runs[run["id"]] = run
        self._append_log(owner, run["id"], "RUN_QUEUED", "Run queued for execution")
        return EnqueuedRun(run_id=run["id"], worker_id=worker["id"], created_at=run["created_at"])

    def run_worker(self, owner: str, worker_id: str) -> EnqueuedRun:
        with self.lock:
            worker = self._worker(owner, worker_id)
            return self._enqueue(owner, worker, worker["url"], None, worker["name"])

    def retry_run(self, owner: str, run_id: str) -> EnqueuedRun:
        with self.lock:
            original = self._run(owner, run_id)
            if original["status"] != "failed":
                raise Problem(409, "INVALID_RETRY_STATE", "Only failed runs can be retried")
            worker = self._worker(owner, original["worker_id"], active=False)
            if worker["archived"]:
                raise Problem(409, "WORKER_ARCHIVED", "Archived workers cannot be retried")
            return self._enqueue(owner, worker, original["url"], run_id, original["worker_name"])

    def list_runs(self, owner: str, worker_id: str | None, status: str | None, page: int, size: int) -> RunPage:
        with self.lock:
            if worker_id is not None:
                self._worker(owner, worker_id, active=False)
            items = sorted((r for r in self.runs.values() if r["owner"] == owner
                            and (worker_id is None or r["worker_id"] == worker_id)
                            and (status is None or r["status"] == status)),
                           key=lambda r: r["created_at"], reverse=True)
            offset = (page - 1) * size
            return RunPage(items=[self._public_run(r) for r in items[offset:offset + size]],
                           total=len(items), limit=size, offset=offset)

    def get_run(self, owner: str, run_id: str) -> Run:
        with self.lock:
            return self._public_run(self._run(owner, run_id))

    def get_logs(self, owner: str, run_id: str, after_id: int) -> RunLogPage:
        with self.lock:
            self._run(owner, run_id)
            rows = [r for r in self.logs if r["run_id"] == run_id and r["id"] > after_id]
            return RunLogPage(items=[RunLog(**{k: v for k, v in r.items() if k not in ("owner", "run_id")}) for r in rows],
                              next_after_id=rows[-1]["id"] if rows else after_id)

    def dashboard(self, owner: str) -> DashboardSummary:
        with self.lock:
            all_runs = sorted((r for r in self.runs.values() if r["owner"] == owner),
                              key=lambda r: r["created_at"], reverse=True)
            since = datetime.now(timezone.utc) - timedelta(hours=24)
            window = [r for r in all_runs if datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) >= since]
            completed = [r for r in window if r["status"] in ("succeeded", "failed")]
            succeeded = sum(r["status"] == "succeeded" for r in completed)
            return DashboardSummary(total=len(window), succeeded=succeeded,
                                    failed=len(completed) - succeeded,
                                    success_rate=succeeded / len(completed) if completed else None,
                                    avg_duration_ms=round(sum(r["duration_ms"] or 0 for r in completed) / len(completed)) if completed else None,
                                    recent_runs=[self._public_run(r) for r in all_runs[:10]])

    def _seed_user(self, owner: str):
        """Populate each new account with clearly labelled sample history."""
        monitor = self.create_worker(owner, WorkerCreate(name="Demo: website monitor", kind="website_monitor", url="https://example.com/"))
        summarizer = self.create_worker(owner, WorkerCreate(name="Demo: URL summarizer", kind="url_summarizer", url="https://example.org/"))
        for worker, status, result, error, event in (
            (monitor, "succeeded", {"http_status": 200, "response_time_ms": 186, "healthy": True}, None, "DEMO_MONITOR_SUCCEEDED"),
            (monitor, "failed", {"http_status": 503, "response_time_ms": 241, "healthy": False},
             {"code": "HTTP_STATUS_ERROR", "message": "Demo response: HTTP 503"}, "DEMO_MONITOR_FAILED"),
            (summarizer, "succeeded", {"source_url": summarizer.url, "page_title": "Example page",
                                      "summary": "## Demo summary\n- This is seeded sample content.\n- No page or model was called.",
                                      "model": "demo/seeded", "input_was_truncated": False}, None, "DEMO_SUMMARY_SUCCEEDED"),
        ):
            timestamp = now()
            run = {"id": str(uuid4()), "owner": owner, "worker_id": worker.id, "worker_name": worker.name,
                   "kind": worker.kind, "status": status, "url": worker.url, "created_at": timestamp,
                   "started_at": timestamp, "finished_at": timestamp, "duration_ms": 186,
                   "result": result, "error": error, "retry_of_run_id": None}
            self.runs[run["id"]] = run
            self._append_log(owner, run["id"], "DEMO_SEEDED", "Sample run; no network or model request was made")
            self._append_log(owner, run["id"], event, "Sample run completed" if status == "succeeded" else "Sample run failed",
                             "INFO" if status == "succeeded" else "ERROR")
