"""SQLite account persistence; worker and run tables follow in Issue #6."""

import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import String, create_engine, select
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


def default_database_url() -> str:
    path = Path(__file__).resolve().parents[1] / "data" / "runguard.db"
    return f"sqlite:///{path}"


class UserRepository:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or os.environ.get("RUNGUARD_DATABASE_URL") or default_database_url()
        if not self.database_url.startswith("sqlite:///"):
            raise ValueError("RunGuard requires a local SQLite database URL")
        if self.database_url != "sqlite:///:memory:":
            Path(self.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(self.database_url, connect_args={"check_same_thread": False})
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def migrate(self) -> None:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", self.database_url)
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
