"""Worker heartbeat reads shared by alerting and the bounded responder."""

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text


def heartbeat_age(users, *, current: datetime | None = None,
                  sqlite_path: Path | None = None) -> float | None:
    current = (current or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if users.engine.dialect.name == "postgresql":
        with users.sessions() as session:
            value = session.scalar(text("SELECT last_seen_at FROM worker_heartbeat WHERE id = 1"))
        if value is None:
            return None
        recorded = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        return max(0.0, (current - recorded).total_seconds())
    if sqlite_path is None:
        path = Path(users.database_url.removeprefix("sqlite:///"))
        sqlite_path = Path(f"{path.resolve()}.worker.lock")
    try:
        return max(0.0, current.timestamp() - sqlite_path.stat().st_mtime)
    except OSError:
        return None
