"""Small transactional guards for local alert and incident state changes."""

from sqlalchemy import text


ALERT_INCIDENT_LOCK = 0x5247414C455254


def serialize(session, *, postgres: bool, key: int) -> None:
    """Hold a transaction-scoped advisory lock on PostgreSQL.

    SQLite remains available to existing isolated tests until the cutover; its
    immediate transaction retains the old single-writer semantics.
    """
    if postgres:
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    else:
        session.execute(text("BEGIN IMMEDIATE"))
