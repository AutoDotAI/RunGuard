"""SQLite-backed dashboard contract checks (SPEC §4.2 and FR-06/FR-09)."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import RunRecord
from app.main import create_app


AT = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
SECRET = "test-only-signing-secret-32-bytes-minimum"
PATH = "/api/v1/dashboard/summary"


def stamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def register(client: TestClient, email: str) -> tuple[str, str]:
    response = client.post("/api/v1/auth/register", json={
        "email": email, "password": "CorrectHorse123!"})
    assert response.status_code == 201, response.text
    body = response.json()
    return body["user"]["id"], body["access_token"]


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def worker(client: TestClient, token: str, name: str) -> str:
    response = client.post("/api/v1/workers", headers=headers(token), json={
        "name": name, "kind": "website_monitor", "url": "https://example.com/"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def record(number: int, owner: str, worker_id: str, status: str,
           created_at: datetime, duration_ms: int | None = None,
           started_at: datetime | None = None, finished_at: datetime | None = None) -> RunRecord:
    return RunRecord(
        id=f"run-{number:032d}", owner_user_id=owner, worker_id=worker_id,
        worker_name_snapshot="Historical worker", kind="website_monitor",
        url_snapshot="https://example.com/", config_snapshot_json="{}",
        status=status, created_at=stamp(created_at),
        started_at=stamp(started_at) if started_at else None,
        finished_at=stamp(finished_at) if finished_at else None,
        duration_ms=duration_ms,
    )


def test_dashboard_rolling_window_archived_history_order_ownership_and_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'dashboard.db'}"
    with TestClient(create_app(secret=SECRET, database_url=database_url)) as client:
        client.app.state.store.clock = lambda: AT
        alice_id, alice = register(client, "alice@example.com")
        bob_id, bob = register(client, "bob@example.com")
        empty = client.get(PATH, headers=headers(alice)).json()
        assert empty == {"window": "24h", "total": 0, "succeeded": 0,
                         "failed": 0, "success_rate": None,
                         "avg_duration_ms": None, "recent_runs": []}
        assert client.get(PATH).status_code == 401
        assert client.get(PATH, headers=headers("invalid")).status_code == 401
        assert client.get(PATH, headers=headers(alice), params={"window": "7d"}).status_code == 400

        archived_worker = worker(client, alice, "Archived")
        running_worker = worker(client, alice, "Running")
        queued_worker = worker(client, alice, "Queued")
        bob_worker = worker(client, bob, "Bob")
        boundary = AT - timedelta(hours=24)
        rows = [
            record(1, alice_id, archived_worker, "failed", boundary - timedelta(microseconds=1), 90),
            record(2, alice_id, archived_worker, "succeeded", boundary, 101),
            record(3, alice_id, archived_worker, "failed", boundary, 201),
            record(4, alice_id, running_worker, "running", AT - timedelta(hours=1)),
            record(5, alice_id, queued_worker, "queued", AT - timedelta(hours=1)),
            record(6, alice_id, archived_worker, "failed", AT - timedelta(hours=2),
                   started_at=AT - timedelta(hours=2),
                   finished_at=AT - timedelta(hours=2) + timedelta(milliseconds=301)),
            record(7, alice_id, archived_worker, "succeeded", AT + timedelta(seconds=1), 100),
        ]
        # Equivalent UTC instants with different ISO precision must use ID tie-breaking.
        rows[3].created_at = (AT - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        rows += [record(number, alice_id, archived_worker, "succeeded",
                        boundary - timedelta(days=number), 50)
                 for number in range(8, 15)]
        rows.append(record(15, bob_id, bob_worker, "succeeded", AT - timedelta(minutes=1), 70))
        with client.app.state.users.sessions() as session:
            session.add_all(rows)
            session.commit()
        assert client.delete(f"/api/v1/workers/{archived_worker}", headers=headers(alice)).status_code == 204

        first = client.get(PATH, headers=headers(alice))
        assert first.status_code == 200, first.text
        summary = first.json()
        assert {key: summary[key] for key in ("total", "succeeded", "failed")} == {
            "total": 5, "succeeded": 1, "failed": 2}
        assert summary["success_rate"] == pytest.approx(1 / 3)
        assert summary["avg_duration_ms"] == 201
        expected = sorted(rows[:-1], key=lambda row: (
            datetime.fromisoformat(row.created_at.replace("Z", "+00:00")), row.id), reverse=True)[:10]
        assert [run["id"] for run in summary["recent_runs"]] == [row.id for row in expected]
        assert any(run["worker_id"] == archived_worker for run in summary["recent_runs"])
        assert any(datetime.fromisoformat(run["created_at"].replace("Z", "+00:00")) < boundary
                   for run in summary["recent_runs"])
        bob_summary = client.get(PATH, headers=headers(bob)).json()
        assert bob_summary["total"] == bob_summary["succeeded"] == 1
        assert bob_summary["failed"] == 0
        assert [run["id"] for run in bob_summary["recent_runs"]] == [rows[-1].id]

    with TestClient(create_app(secret=SECRET, database_url=database_url)) as client:
        client.app.state.store.clock = lambda: AT
        assert client.get(PATH, headers=headers(alice)).json() == summary
        assert client.get(PATH, headers=headers(bob)).json() == bob_summary


def test_active_only_window_has_null_rates_and_missing_terminal_duration_is_explicit(tmp_path):
    with TestClient(create_app(secret=SECRET, database_url=f"sqlite:///{tmp_path / 'dashboard.db'}")) as client:
        client.app.state.store.clock = lambda: AT
        owner, token = register(client, "owner@example.com")
        worker_id = worker(client, token, "Check")
        with client.app.state.users.sessions() as session:
            session.add(record(20, owner, worker_id, "queued", AT - timedelta(minutes=5)))
            session.commit()
        summary = client.get(PATH, headers=headers(token)).json()
        assert summary["total"] == 1
        assert summary["succeeded"] == summary["failed"] == 0
        assert summary["success_rate"] is summary["avg_duration_ms"] is None

        with client.app.state.users.sessions() as session:
            session.add(record(21, owner, worker_id, "failed", AT - timedelta(minutes=1)))
            session.commit()
        response = client.get(PATH, headers=headers(token))
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "DASHBOARD_DATA_INTEGRITY"
