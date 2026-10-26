"""Single-process durable queue runner."""

import argparse
import fcntl
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

from sqlalchemy import select, text

from app.db import RunRecord, UserRepository
from app.models import MonitorResult, SummarizerResult
from app.run_logs import new_run_log, safe_event
from app.store import now
from .types import HandlerOutcome, RunSnapshot
from .website_monitor import monitor
from .url_summarizer import summarize


Handler = Callable[[RunSnapshot], HandlerOutcome]


def _elapsed_ms(start: str | None, finish: str) -> int:
    if start is None:
        return 0
    start_time = datetime.fromisoformat(start.replace("Z", "+00:00"))
    end_time = datetime.fromisoformat(finish.replace("Z", "+00:00"))
    return max(0, round((end_time - start_time).total_seconds() * 1000))


class WorkerAlreadyRunning(RuntimeError):
    pass


class WorkerRunner:
    def __init__(self, database_url: str | None = None,
                 handlers: Mapping[str, Handler] | None = None):
        self.db = UserRepository(database_url)
        path = self.db.database_url.removeprefix("sqlite:///")
        if path == ":memory:":
            raise ValueError("Worker requires a persistent SQLite file")
        self.lock_path = Path(str(Path(path).resolve()) + ".worker.lock")
        self.handlers = {"website_monitor": monitor,
                         "url_summarizer": lambda snapshot: summarize(
                             snapshot, log=lambda event, message, level:
                             self._monitor_step(snapshot.id, event, message, level)),
                         **(handlers or {})}
        self._lock_file = None

    def __enter__(self) -> "WorkerRunner":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = self.lock_path.open("a+")
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock_file.close()
            self._lock_file = None
            self.db.close()
            raise WorkerAlreadyRunning("Another worker owns this SQLite queue") from None
        try:
            self.db.migrate()
            self.reconcile_interrupted()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._lock_file is not None:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None
        self.db.close()

    def _require_lock(self) -> None:
        if self._lock_file is None:
            raise RuntimeError("WorkerRunner must be entered before processing")

    @staticmethod
    def _log(session, run: RunRecord, timestamp: str, level: str,
             event: str, message: str) -> None:
        session.add(new_run_log(run.id, run.owner_user_id, timestamp,
                                level, event, message))

    def reconcile_interrupted(self) -> int:
        self._require_lock()
        with self.db.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            runs = session.scalars(select(RunRecord).where(RunRecord.status == "running")).all()
            for run in runs:
                finished = now()
                run.status = "failed"
                run.finished_at = finished
                run.duration_ms = _elapsed_ms(run.started_at, finished)
                run.error_code = "WORKER_INTERRUPTED"
                run.error_message = "Worker stopped before the run completed"
                self._log(session, run, finished, "ERROR", "WORKER_INTERRUPTED",
                          "Worker stopped before the run completed")
                self._log(session, run, finished, "ERROR", "RUN_FAILED", "Run failed")
            session.commit()
            return len(runs)

    def claim_oldest(self) -> RunSnapshot | None:
        self._require_lock()
        with self.db.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            # A write lock protects both the select and state change from other writers.
            run = session.scalar(select(RunRecord).where(RunRecord.status == "queued")
                                 .order_by(RunRecord.created_at, RunRecord.id).limit(1))
            if run is None:
                session.commit()
                return None
            timestamp = now()
            run.status = "running"
            run.started_at = timestamp
            self._log(session, run, timestamp, "INFO", "RUN_CLAIMED", "Run claimed by worker")
            self._log(session, run, timestamp, "INFO", "RUN_STARTED", "Run started")
            snapshot = RunSnapshot(run.id, run.kind, run.url_snapshot, run.config_snapshot_json)
            session.commit()
            return snapshot

    def _finish(self, run_id: str, outcome: HandlerOutcome | None,
                error_code: str | None = None, error_message: str | None = None) -> bool:
        with self.db.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            run = session.get(RunRecord, run_id)
            if run is None or run.status != "running":
                session.commit()
                return False
            finished = now()
            run.finished_at = finished
            run.duration_ms = _elapsed_ms(run.started_at, finished)
            if outcome is not None:
                run.result_json = json.dumps(outcome.result) if outcome.result is not None else None
                run.http_status = outcome.http_status
            if error_code is None:
                run.status = "succeeded"
                if run.kind == "url_summarizer":
                    self._log(session, run, finished, "INFO", "SUMMARY_SAVED", "")
                self._log(session, run, finished, "INFO", "RUN_SUCCEEDED", "Run succeeded")
            else:
                run.status = "failed"
                run.error_code, run.error_message = safe_event(error_code, error_message or "")
                self._log(session, run, finished, "ERROR", run.error_code, run.error_message)
                self._log(session, run, finished, "ERROR", "RUN_FAILED", "Run failed")
            session.commit()
            return True

    def _monitor_step(self, run_id: str, event: str, message: str,
                      level: str = "INFO") -> None:
        with self.db.sessions() as session:
            run = session.get(RunRecord, run_id)
            if run is not None and run.status == "running":
                self._log(session, run, now(), level, event, message)
                session.commit()

    def run_once(self) -> bool:
        snapshot = self.claim_oldest()
        if snapshot is None:
            return False
        handler = self.handlers.get(snapshot.kind)
        if handler is None:
            self._finish(snapshot.id, None, "HANDLER_UNAVAILABLE",
                         "No handler is available for this worker type")
            return True
        if snapshot.kind == "website_monitor":
            self._monitor_step(snapshot.id, "URL_VALIDATION_STARTED", "")
            self._monitor_step(snapshot.id, "HTTP_REQUEST_STARTED", "Checking configured website")
        try:
            outcome = handler(snapshot)
            if not isinstance(outcome, HandlerOutcome):
                raise TypeError("Handler must return HandlerOutcome")
            # Check serializability before the terminal write transaction.
            json.dumps(outcome.result)
            result_type = {"website_monitor": MonitorResult,
                           "url_summarizer": SummarizerResult}[snapshot.kind]
            if outcome.result is not None:
                result_type.model_validate(outcome.result)
            elif outcome.error_code is None:
                raise ValueError("Successful handler outcome requires a result")
        except Exception:
            # Handler errors may contain credentials or fetched page content.
            if snapshot.kind == "website_monitor":
                self._monitor_step(snapshot.id, "HTTP_REQUEST_FAILED", "Website check failed", "ERROR")
            self._finish(snapshot.id, None, "HANDLER_ERROR", "Worker handler failed")
        else:
            if snapshot.kind == "website_monitor":
                if outcome.http_status is not None:
                    self._monitor_step(snapshot.id, "HTTP_RESPONSE_RECEIVED",
                                       f"Received HTTP {outcome.http_status}",
                                       "INFO" if outcome.error_code is None else "ERROR")
                else:
                    self._monitor_step(snapshot.id, "HTTP_REQUEST_FAILED", "Website check failed", "ERROR")
            self._finish(snapshot.id, outcome, outcome.error_code, outcome.error_message)
        return True

    def poll(self, interval_seconds: float = 1.0) -> None:
        while True:
            if not self.run_once():
                time.sleep(interval_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="RunGuard SQLite queue worker")
    parser.add_argument("--once", action="store_true", help="process at most one queued run")
    args = parser.parse_args()
    try:
        with WorkerRunner() as runner:
            if args.once:
                runner.run_once()
            else:
                runner.poll()
    except WorkerAlreadyRunning as exc:
        parser.exit(1, f"{exc}\n")
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
