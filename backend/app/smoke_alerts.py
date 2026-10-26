"""Interactive local-only failure and recovery smoke; never fetches a URL."""

import getpass
import os
from datetime import datetime, timezone

from sqlalchemy import func, select

from .alerting import AlertService
from .auth import verify_password
from .db import RunRecord, UserRepository
from .models import WorkerCreate
from .operator_alerts import OperatorPublisher
from .store import PersistentStore
from .workers.runner import WorkerRunner
from .workers.types import HandlerOutcome


def main() -> None:
    url = os.environ.get("RUNGUARD_ALERTMANAGER_URL")
    if not url:
        raise SystemExit("Start the observability profile before the alert smoke")
    users = UserRepository()
    users.migrate()
    email = input("Existing RunGuard account email: ").strip()
    password = getpass.getpass("Password (not logged): ")
    user = users.by_email(email)
    if user is None or not verify_password(password, user["password_hash"]):
        raise SystemExit("Invalid account credentials")
    owner = user["id"]
    with users.sessions() as session:
        active = session.scalar(select(func.count()).select_from(RunRecord).where(
            RunRecord.status.in_(("queued", "running")))) or 0
    if active:
        raise SystemExit("Stop the worker and clear existing queued/running jobs before this smoke")
    store = PersistentStore(users)
    worker = store.create_worker(owner, WorkerCreate(name="Local alert smoke", kind="website_monitor",
                                                     url="https://example.com/"))
    service = AlertService(users)
    publisher = OperatorPublisher(url)
    for _ in range(2):
        queued = store.run_worker(owner, worker.id)
        with WorkerRunner(users.database_url, {"website_monitor": lambda _run: HandlerOutcome(
                result=None, http_status=503, error_code="HTTP_STATUS_ERROR",
                error_message="Configured target returned HTTP 503")}) as runner:
            assert runner.run_once()
        assert store.get_run(owner, queued.run_id).status == "failed"
    counts = service.evaluate()
    assert counts["repeated_run_failure"] >= 1
    publisher(counts)
    alert = next(row for row in service.list_alerts(owner).items if row.kind == "repeated_run_failure" and row.status == "open")
    print(f"Open alert for run {alert.run_id}. View localhost:3000/dashboard and localhost:9094 now.")
    input("Press Enter after inspecting the alert to create a successful recovery run...")
    queued = store.run_worker(owner, worker.id)
    with WorkerRunner(users.database_url, {"website_monitor": lambda _run: HandlerOutcome(
            result={"http_status": 200, "response_time_ms": 1, "healthy": True}, http_status=200)}) as runner:
        assert runner.run_once()
    assert store.get_run(owner, queued.run_id).status == "succeeded"
    counts = service.evaluate(datetime.now(timezone.utc))
    assert counts["repeated_run_failure"] == 0
    publisher(counts)
    print("Alert resolved. Refresh the dashboard and localhost:9094 to inspect recovery.")
    users.close()


if __name__ == "__main__":
    main()
