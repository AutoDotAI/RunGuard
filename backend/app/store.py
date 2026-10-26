"""Owned worker, run, and log operations backed by the configured database."""

import json
import os
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Callable
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import RunLogRecord, RunRecord, UserRecord, UserRepository, WorkerRecord
from .run_logs import new_run_log
from . import observability
from .models import (
    DashboardSummary, EnqueuedRun, Run, RunLog, RunLogPage, RunPage,
    Worker, WorkerCreate, WorkerPage, WorkerUpdate,
)
from .outbound import OutboundError, enforce_allowed_host, parse_public_url


SQLITE_MAX_ROWID = 2**63 - 1


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Problem(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def validate_public_url(value: str) -> str:
    """Use the worker's URL syntax policy without DNS in the API request path."""
    try:
        parsed = parse_public_url(value)
        enforce_allowed_host(parsed)
        return parsed.value
    except OutboundError as exc:
        raise Problem(422, "VALIDATION_ERROR", exc.message) from None


class PersistentStore:
    def __init__(self, users: UserRepository,
                 clock: Callable[[], datetime] | None = None):
        self.users = users
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        # This lock limits contention within one process. PostgreSQL row locks
        # serialize per-owner worker quota checks across API processes.
        self.lock = RLock()

    def create_user(self, email: str, password_hash: str) -> dict:
        user = self.users.create(email, password_hash, now())
        if user is None:
            raise Problem(409, "ACCOUNT_EXISTS", "An account with this email already exists")
        return user

    def user_by_email(self, email: str) -> dict | None:
        return self.users.by_email(email)

    def user_by_id(self, user_id: str) -> dict | None:
        return self.users.by_id(user_id)

    @staticmethod
    def _owned_worker(session: Session, owner: str, worker_id: str, active: bool = True,
                      for_update: bool = False) -> WorkerRecord:
        query = select(WorkerRecord).where(
            WorkerRecord.id == worker_id, WorkerRecord.owner_user_id == owner)
        if for_update and session.bind.dialect.name == "postgresql":
            query = query.with_for_update()
        worker = session.scalar(query)
        if worker is None or (active and worker.archived_at is not None):
            raise Problem(404, "NOT_FOUND", "Worker not found")
        return worker

    @staticmethod
    def _owned_run(session: Session, owner: str, run_id: str) -> RunRecord:
        run = session.scalar(select(RunRecord).where(
            RunRecord.id == run_id, RunRecord.owner_user_id == owner))
        if run is None:
            raise Problem(404, "NOT_FOUND", "Run not found")
        return run

    @staticmethod
    def _public_run(run: RunRecord) -> Run:
        return Run(
            id=run.id, worker_id=run.worker_id, worker_name=run.worker_name_snapshot,
            kind=run.kind, status=run.status, url=run.url_snapshot,
            created_at=run.created_at, started_at=run.started_at,
            finished_at=run.finished_at, duration_ms=run.duration_ms,
            result=json.loads(run.result_json) if run.result_json else None,
            error={"code": run.error_code, "message": run.error_message} if run.error_code else None,
            retry_of_run_id=run.retry_of_run_id,
        )

    @staticmethod
    def _public_worker(session: Session, worker: WorkerRecord) -> Worker:
        latest = session.scalar(select(RunRecord).where(
            RunRecord.worker_id == worker.id,
            RunRecord.owner_user_id == worker.owner_user_id,
        ).order_by(RunRecord.created_at.desc(), RunRecord.id.desc()).limit(1))
        last = {"id": latest.id, "status": latest.status, "created_at": latest.created_at} if latest else None
        return Worker(id=worker.id, name=worker.name, kind=worker.kind,
                      url=worker.url, created_at=worker.created_at,
                      updated_at=worker.updated_at, last_run=last)

    def list_workers(self, owner: str, limit: int, offset: int) -> WorkerPage:
        with self.users.sessions() as session:
            condition = (WorkerRecord.owner_user_id == owner, WorkerRecord.archived_at.is_(None))
            total = session.scalar(select(func.count()).select_from(WorkerRecord).where(*condition)) or 0
            records = session.scalars(select(WorkerRecord).where(*condition).order_by(
                WorkerRecord.created_at.desc(), WorkerRecord.id.desc()).limit(limit).offset(offset)).all()
            return WorkerPage(items=[self._public_worker(session, record) for record in records],
                              total=total, limit=limit, offset=offset)

    def get_worker(self, owner: str, worker_id: str) -> Worker:
        with self.users.sessions() as session:
            return self._public_worker(session, self._owned_worker(session, owner, worker_id))

    def create_worker(self, owner: str, payload: WorkerCreate) -> Worker:
        if (os.environ.get("RUNGUARD_PUBLIC_MODE") == "1" and
                os.environ.get("RUNGUARD_SUMMARIZER_AVAILABLE", "").lower() not in {"1", "true"} and
                payload.kind == "url_summarizer"):
            raise Problem(422, "FEATURE_UNAVAILABLE", "Live summaries are unavailable in this demo")
        url = validate_public_url(payload.url)
        with self.lock, self.users.sessions() as session:
            if session.bind.dialect.name == "postgresql":
                # All worker creations for one account serialize here, so two
                # API processes cannot both pass the 20-worker quota check.
                session.scalar(select(UserRecord.id).where(UserRecord.id == owner).with_for_update())
            total = session.scalar(select(func.count()).select_from(WorkerRecord).where(
                WorkerRecord.owner_user_id == owner, WorkerRecord.archived_at.is_(None))) or 0
            if total >= 20:
                raise Problem(429, "WORKER_LIMIT_REACHED", "You can save at most 20 workers")
            timestamp = now()
            record = WorkerRecord(id=str(uuid4()), owner_user_id=owner,
                                  name=payload.name, kind=payload.kind, url=url,
                                  config_json="{}", created_at=timestamp,
                                  updated_at=timestamp, archived_at=None)
            session.add(record)
            session.commit()
            return self._public_worker(session, record)

    def update_worker(self, owner: str, worker_id: str, payload: WorkerUpdate) -> Worker:
        if not payload.model_fields_set:
            raise Problem(422, "VALIDATION_ERROR", "At least one field is required")
        if any(getattr(payload, field) is None for field in payload.model_fields_set):
            raise Problem(422, "VALIDATION_ERROR", "Fields cannot be null")
        url = validate_public_url(payload.url) if payload.url is not None else None
        with self.users.sessions() as session:
            record = self._owned_worker(session, owner, worker_id, for_update=True)
            if payload.name is not None:
                record.name = payload.name
            if url is not None:
                record.url = url
            record.updated_at = now()
            session.commit()
            return self._public_worker(session, record)

    def archive_worker(self, owner: str, worker_id: str) -> None:
        with self.users.sessions() as session:
            record = self._owned_worker(session, owner, worker_id, for_update=True)
            record.archived_at = now()
            session.commit()

    def _enqueue(self, session: Session, owner: str, worker: WorkerRecord,
                 url: str, retry_of: str | None, name: str,
                 config_snapshot_json: str, kind: str) -> EnqueuedRun:
        active_count = session.scalar(select(func.count()).select_from(RunRecord).where(
            RunRecord.owner_user_id == owner,
            RunRecord.status.in_(("queued", "running")))) or 0
        existing = session.scalar(select(RunRecord).where(
            RunRecord.worker_id == worker.id,
            RunRecord.status.in_(("queued", "running"))).limit(1))
        if existing:
            raise Problem(409, "RUN_ALREADY_ACTIVE", "This worker already has an active run",
                          {"run_id": existing.id})
        if active_count >= 10:
            raise Problem(429, "TOO_MANY_ACTIVE_RUNS", "At most 10 runs may be active")
        with observability.tracer().start_as_current_span("db.enqueue"):
            timestamp = now()
            run = RunRecord(id=str(uuid4()), worker_id=worker.id, owner_user_id=owner,
                            worker_name_snapshot=name, kind=kind, url_snapshot=url,
                            config_snapshot_json=config_snapshot_json, status="queued",
                            created_at=timestamp, started_at=None, finished_at=None,
                            duration_ms=None, http_status=None, result_json=None,
                            error_code=None, error_message=None, retry_of_run_id=retry_of,
                            trace_id=observability.trace_id(), traceparent=observability.traceparent())
            try:
                session.add(run)
                session.flush()  # Insert the parent before its foreign-keyed log entry.
                session.add(new_run_log(run.id, owner, timestamp, "INFO", "RUN_QUEUED"))
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(select(RunRecord).where(
                    RunRecord.worker_id == worker.id,
                    RunRecord.status.in_(("queued", "running"))).limit(1))
                if existing:
                    raise Problem(409, "RUN_ALREADY_ACTIVE", "This worker already has an active run",
                                  {"run_id": existing.id}) from None
                raise
            observability.record_enqueue(kind)
            observability.emit("RUN_QUEUED", run_id=run.id)
        return EnqueuedRun(run_id=run.id, worker_id=worker.id,
                           status="queued", created_at=timestamp)

    def run_worker(self, owner: str, worker_id: str) -> EnqueuedRun:
        with self.lock, self.users.sessions() as session:
            # Claim SQLite's only writer slot before reading either quota. A
            # second API process waits, then observes the committed first run.
            session.execute(text("BEGIN IMMEDIATE"))
            worker = self._owned_worker(session, owner, worker_id)
            return self._enqueue(session, owner, worker, worker.url, None,
                                 worker.name, worker.config_json, worker.kind)

    def retry_run(self, owner: str, run_id: str) -> EnqueuedRun:
        with self.lock, self.users.sessions() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            original = self._owned_run(session, owner, run_id)
            if original.status != "failed":
                raise Problem(409, "INVALID_RETRY_STATE", "Only failed runs can be retried")
            worker = self._owned_worker(session, owner, original.worker_id, active=False)
            if worker.archived_at is not None:
                raise Problem(409, "WORKER_ARCHIVED", "Archived workers cannot be retried")
            return self._enqueue(session, owner, worker, original.url_snapshot,
                                 run_id, original.worker_name_snapshot,
                                 original.config_snapshot_json, original.kind)

    def list_runs(self, owner: str, worker_id: str | None, status: str | None,
                  page: int, size: int) -> RunPage:
        with self.users.sessions() as session:
            if worker_id is not None:
                self._owned_worker(session, owner, worker_id, active=False)
            conditions = [RunRecord.owner_user_id == owner]
            if worker_id is not None:
                conditions.append(RunRecord.worker_id == worker_id)
            if status is not None:
                conditions.append(RunRecord.status == status)
            total = session.scalar(select(func.count()).select_from(RunRecord).where(*conditions)) or 0
            offset = (page - 1) * size
            records = session.scalars(select(RunRecord).where(*conditions).order_by(
                RunRecord.created_at.desc(), RunRecord.id.desc()).limit(size).offset(offset)).all()
            return RunPage(items=[self._public_run(run) for run in records],
                           total=total, limit=size, offset=offset)

    def get_run(self, owner: str, run_id: str) -> Run:
        with self.users.sessions() as session:
            return self._public_run(self._owned_run(session, owner, run_id))

    def get_logs(self, owner: str, run_id: str, after_id: int) -> RunLogPage:
        with self.users.sessions() as session:
            self._owned_run(session, owner, run_id)
            # SQLite row IDs cannot exceed signed 64-bit integers. The API
            # accepts any nonnegative integer cursor, so larger values are
            # valid empty pages and must never be bound into a SQLite query.
            if after_id > SQLITE_MAX_ROWID:
                return RunLogPage(items=[], next_after_id=after_id)
            records = session.scalars(select(RunLogRecord).where(
                RunLogRecord.run_id == run_id,
                RunLogRecord.owner_user_id == owner,
                RunLogRecord.id > after_id,
            ).order_by(RunLogRecord.id)).all()
            return RunLogPage(items=[RunLog(id=row.id, timestamp=row.timestamp,
                                             level=row.level, event=row.event,
                                             message=row.message) for row in records],
                              next_after_id=records[-1].id if records else after_id)

    def dashboard(self, owner: str) -> DashboardSummary:
        evaluated_at = self.clock().astimezone(timezone.utc)
        since = evaluated_at - timedelta(hours=24)
        with self.users.sessions() as session:
            all_runs = session.scalars(select(RunRecord).where(
                RunRecord.owner_user_id == owner)).all()
            all_runs.sort(key=lambda run: (parse_timestamp(run.created_at), run.id), reverse=True)
            window = [run for run in all_runs if since <= parse_timestamp(run.created_at) <= evaluated_at]
            completed = [run for run in window if run.status in ("succeeded", "failed")]
            succeeded = sum(run.status == "succeeded" for run in completed)
            durations = []
            for run in completed:
                duration = run.duration_ms
                if duration is None and run.finished_at is not None and (run.started_at is not None or run.status == "failed"):
                    # FR-06 permits timestamp recovery; queued failures use queue age.
                    start = parse_timestamp(run.started_at or run.created_at)
                    finish = parse_timestamp(run.finished_at)
                    duration = round((finish - start).total_seconds() * 1000)
                if duration is None or duration < 0:
                    raise Problem(500, "DASHBOARD_DATA_INTEGRITY", "Completed run has no valid duration")
                durations.append(duration)
            return DashboardSummary(window="24h", total=len(window), succeeded=succeeded,
                                    failed=len(completed) - succeeded,
                                    success_rate=succeeded / len(completed) if completed else None,
                                    avg_duration_ms=round(sum(durations) / len(durations)) if completed else None,
                                    recent_runs=[self._public_run(run) for run in all_runs[:10]])
