"""Evaluate durable, owner-scoped user-impact alerts from database run state."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import Event, Thread
from typing import Callable
from uuid import uuid4

from sqlalchemy import func, select

from .db import AlertRecord, RunRecord, UserRepository
from .db_serialization import serialize
from .worker_health import heartbeat_age
from .models import Alert, AlertPage
from . import observability


FAILURE_WINDOW = timedelta(minutes=5)
QUEUE_LIMIT = timedelta(seconds=60)
HEARTBEAT_LIMIT = timedelta(seconds=15)
KINDS = ("repeated_run_failure", "worker_queue_stalled")


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def fingerprint(owner: str, kind: str, run_id: str | None = None) -> str:
    return sha256(f"{owner}:{kind}:{run_id or ''}".encode()).hexdigest()


class AlertService:
    def __init__(self, users: UserRepository, clock: Callable[[], datetime] | None = None,
                 heartbeat_path: Path | None = None):
        self.users = users
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        path = users.database_url.removeprefix("sqlite:///")
        self.heartbeat_path = heartbeat_path or Path(f"{Path(path).resolve()}.worker.lock")

    @staticmethod
    def _public(session, record: AlertRecord) -> Alert:
        run = session.get(RunRecord, record.run_id)
        return Alert(id=record.id, kind=record.kind, severity=record.severity,
                     status=record.status, title=record.title, run_id=record.run_id,
                     symptom=record.symptom, fingerprint=record.fingerprint,
                     created_at=record.created_at, resolved_at=record.resolved_at,
                     trace_id=run.trace_id if run else None)

    def list_alerts(self, owner: str) -> AlertPage:
        with self.users.sessions() as session:
            rows = session.scalars(select(AlertRecord).where(AlertRecord.owner_user_id == owner)
                                   .order_by(AlertRecord.created_at.desc(), AlertRecord.id.desc())).all()
            return AlertPage(items=[self._public(session, row) for row in rows], total=len(rows))

    def _heartbeat_unhealthy(self, current: datetime) -> bool:
        age = heartbeat_age(self.users, current=current, sqlite_path=self.heartbeat_path)
        return age is None or age > HEARTBEAT_LIMIT.total_seconds()

    def evaluate(self, current: datetime | None = None) -> dict[str, int]:
        """Reconcile open alerts atomically; return aggregate open counts by fixed kind."""
        current = (current or self.clock()).astimezone(timezone.utc)
        active: dict[str, tuple[str, str, str, str, str, str]] = {}
        unhealthy = self._heartbeat_unhealthy(current)
        with self.users.sessions() as session:
            serialize(session, postgres=self.users.engine.dialect.name == "postgresql",
                      key=0x5247414C455254)
            # Read only the active rule window. Leave one second of padding for
            # timestamps whose ISO representations differ in fractional precision;
            # exact boundary checks below still use parsed UTC datetimes.
            floor = stamp(current - FAILURE_WINDOW - timedelta(seconds=1))
            ceiling = stamp(current + timedelta(seconds=1))
            recent_rows = session.scalars(select(RunRecord).where(
                RunRecord.status.in_(("failed", "succeeded")),
                RunRecord.finished_at >= floor, RunRecord.finished_at <= ceiling)
                .order_by(RunRecord.finished_at.desc()).limit(10000)).all()
            failures = [run for run in recent_rows if run.status == "failed"]
            successes = [run for run in recent_rows if run.status == "succeeded"]
            last_success: dict[str, datetime] = {}
            for run in successes:
                finished = utc(run.finished_at)
                if finished <= current and finished > last_success.get(run.owner_user_id, datetime.min.replace(tzinfo=timezone.utc)):
                    last_success[run.owner_user_id] = finished
            recent: dict[str, list[RunRecord]] = {}
            for run in failures:
                finished = utc(run.finished_at)
                if current - FAILURE_WINDOW <= finished <= current:
                    recent.setdefault(run.owner_user_id, []).append(run)
            for owner, runs in recent.items():
                latest = max(runs, key=lambda run: (utc(run.finished_at), run.id))
                recovered_at = last_success.get(owner)
                if recovered_at and recovered_at > utc(latest.finished_at):
                    continue
                if recovered_at and recovered_at < utc(latest.finished_at):
                    runs = [run for run in runs if utc(run.finished_at) > recovered_at]
                if len({run.id for run in runs}) >= 2:
                    fp = fingerprint(owner, KINDS[0])
                    active[fp] = (owner, KINDS[0], "warning", "Repeated job failures",
                                  latest.id, "At least two jobs failed in five minutes; inspect the linked run.")
            if unhealthy:
                queued = session.scalars(select(RunRecord).where(
                    RunRecord.status == "queued",
                    RunRecord.created_at <= stamp(current - QUEUE_LIMIT + timedelta(seconds=1)))
                    .order_by(RunRecord.created_at).limit(10000)).all()
                for run in queued:
                    if current - utc(run.created_at) > QUEUE_LIMIT:
                        fp = fingerprint(run.owner_user_id, KINDS[1], run.id)
                        active[fp] = (run.owner_user_id, KINDS[1], "critical",
                                      "Worker queue stalled", run.id,
                                      "Run remained queued while the worker heartbeat was unhealthy.")

            existing = session.scalars(select(AlertRecord).where(AlertRecord.status == "open")
                                       .limit(10000)).all()
            open_by_fingerprint = {row.fingerprint: row for row in existing}
            for fp, (owner, kind, severity, title, run_id, symptom) in active.items():
                if fp not in open_by_fingerprint:
                    session.add(AlertRecord(id=str(uuid4()), owner_user_id=owner, kind=kind,
                                            severity=severity, status="open", title=title,
                                            run_id=run_id, symptom=symptom, fingerprint=fp,
                                            created_at=stamp(current), resolved_at=None))
                    observability.emit("ALERT_OPENED")
            for fp, row in open_by_fingerprint.items():
                if fp not in active:
                    row.status = "resolved"
                    row.resolved_at = stamp(current)
                    observability.emit("ALERT_RESOLVED")
            session.commit()
            counts = {kind: session.scalar(select(func.count()).select_from(AlertRecord).where(
                AlertRecord.status == "open", AlertRecord.kind == kind)) or 0 for kind in KINDS}
        return counts


class AlertLoop:
    """API-owned timer so alerts change while no browser or worker is running."""

    def __init__(self, service: AlertService, interval: float = 10,
                 publish: Callable[[dict[str, int]], None] | None = None):
        self.service = service
        self.interval = interval
        self.publish = publish
        self.stop_event = Event()
        self.thread: Thread | None = None

    def start(self) -> None:
        self.thread = Thread(target=self._run, name="runguard-alert-evaluator", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=min(self.interval + 1, 5))

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval):
            try:
                counts = self.service.evaluate()
                if self.publish is not None:
                    self.publish(counts)
            except Exception:
                # A broken notification route must not stop future evaluations.
                observability.emit("ALERT_EVALUATION_FAILED", level="ERROR")
