"""RunGuard persistence models and repository adapters."""

import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, Text, create_engine, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class UserRecord(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[str] = mapped_column(String(35), nullable=False)


class WorkerRecord(Base):
    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[str] = mapped_column(String(35), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(35), nullable=False)
    archived_at: Mapped[str | None] = mapped_column(String(35), nullable=True)

    __table_args__ = (Index("ix_workers_owner_archived", "owner_user_id", "archived_at"),)


class RunRecord(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    worker_id: Mapped[str] = mapped_column(String(36), ForeignKey("workers.id"), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    worker_name_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    url_snapshot: Mapped[str] = mapped_column(String(2048), nullable=False)
    config_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[str] = mapped_column(String(35), nullable=False)
    started_at: Mapped[str | None] = mapped_column(String(35), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(35), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_of_run_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("runs.id"), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    traceparent: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        Index("ix_runs_owner_created", "owner_user_id", "created_at"),
        Index("ix_runs_worker_created", "worker_id", "created_at"),
        Index("ix_runs_worker_status", "worker_id", "status"),
        Index("uq_runs_one_active_per_worker", "worker_id", unique=True,
              sqlite_where=text("status IN ('queued', 'running')"),
              postgresql_where=text("status IN ('queued', 'running')")),
    )


class RunLogRecord(Base):
    __tablename__ = "run_logs"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"),
                                    primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("runs.id"), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    timestamp: Mapped[str] = mapped_column(String(35), nullable=False)
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    event: Mapped[str] = mapped_column(String(80), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_run_logs_run_id", "run_id", "id"),
        Index("ix_run_logs_owner_id", "owner_user_id", "id"),
    )


class WorkerHeartbeatRecord(Base):
    __tablename__ = "worker_heartbeat"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_seen_at: Mapped[str] = mapped_column(String(35), nullable=False)
    capabilities_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class AlertRecord(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("runs.id"), nullable=False)
    symptom: Mapped[str] = mapped_column(String(256), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(35), nullable=False)
    resolved_at: Mapped[str | None] = mapped_column(String(35), nullable=True)

    __table_args__ = (
        Index("ix_alerts_owner_created", "owner_user_id", "created_at"),
        Index("uq_alerts_open_fingerprint", "fingerprint", unique=True,
              sqlite_where=text("status = 'open'"),
              postgresql_where=text("status = 'open'")),
    )


class IncidentRecord(Base):
    __tablename__ = "responder_incidents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alert_id: Mapped[str] = mapped_column(String(36), ForeignKey("alerts.id"), unique=True, nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("runs.id"), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    symptom: Mapped[str] = mapped_column(String(256), nullable=False)
    likely_cause: Mapped[str] = mapped_column(String(80), nullable=False)
    recommendation: Mapped[str] = mapped_column(String(256), nullable=False)
    decision_source: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[str] = mapped_column(String(35), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(35), nullable=False)


class ResponderActionRecord(Base):
    __tablename__ = "responder_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(36), ForeignKey("responder_incidents.id"), unique=True, nullable=False)
    alert_id: Mapped[str] = mapped_column(String(36), ForeignKey("alerts.id"), nullable=False)
    actor: Mapped[str] = mapped_column(String(32), nullable=False)
    policy: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(80), nullable=False)
    before_status: Mapped[str] = mapped_column(String(16), nullable=False)
    after_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    result: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[str] = mapped_column(String(35), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(35), nullable=False)


class IncidentAnalysisRecord(Base):
    __tablename__ = "incident_analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(36), ForeignKey("responder_incidents.id"), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(40))
    evidence_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    advisory: Mapped[str | None] = mapped_column(Text)
    content_digest: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[str] = mapped_column(String(35), nullable=False)
    finished_at: Mapped[str | None] = mapped_column(String(35))

    __table_args__ = (Index("uq_incident_analysis_attempt", "incident_id", "attempt", unique=True),)


def default_database_url() -> str:
    path = Path(__file__).resolve().parents[1] / "data" / "runguard.db"
    return f"sqlite:///{path}"


class UserRepository:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.environ.get("RUNGUARD_DATABASE_URL") or default_database_url()
        if self.database_url.startswith("sqlite:///"):
            if self.database_url != "sqlite:///:memory:":
                Path(self.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
            self.engine = create_engine(self.database_url,
                                        connect_args={"check_same_thread": False, "timeout": 30.0})

            @event.listens_for(self.engine, "connect")
            def _enable_foreign_keys(connection, _record):
                connection.execute("PRAGMA foreign_keys=ON")
        elif self.database_url.startswith("postgresql+psycopg://"):
            self.engine = create_engine(self.database_url, pool_pre_ping=True)
        else:
            raise ValueError("RunGuard requires a SQLite or postgresql+psycopg database URL")
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def migrate(self) -> None:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.attributes["database_url"] = self.database_url
        command.upgrade(config, "head")

    @staticmethod
    def _public(user: UserRecord) -> dict[str, str]:
        return {"id": user.id, "email": user.email, "password_hash": user.password_hash,
                "created_at": user.created_at}

    def create(self, email: str, password_hash: str, created_at: str) -> dict[str, str] | None:
        normalized = email.strip().lower()
        with self.sessions() as session:
            user = UserRecord(id=str(uuid4()), email=normalized,
                              password_hash=password_hash, created_at=created_at)
            session.add(user)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return None
            return self._public(user)

    def by_email(self, email: str) -> dict[str, str] | None:
        with self.sessions() as session:
            user = session.scalar(select(UserRecord).where(UserRecord.email == email.strip().lower()))
            return self._public(user) if user else None

    def by_id(self, user_id: str) -> dict[str, str] | None:
        with self.sessions() as session:
            user = session.get(UserRecord, user_id)
            return self._public(user) if user else None

    def close(self) -> None:
        self.engine.dispose()
