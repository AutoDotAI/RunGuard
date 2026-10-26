"""Local-only, single-run worker pause drill. Never import this in the public API."""

import argparse
import fcntl
import json
import os
import time
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, text

from .db import RunRecord, UserRepository
from .outbound import FetchResponse
from .run_logs import new_run_log
from .store import now
from .workers.runner import WorkerRunner
from .workers.website_monitor import monitor


class DemoDenied(RuntimeError):
    pass


def require_demo_mode() -> None:
    project = os.environ.get("RUNGUARD_DEMO_PROJECT", "")
    if (os.environ.get("RUNGUARD_DEMO_MODE", "").lower() != "true"
            or not project.startswith("rg-demo-")
            or os.environ.get("RUNGUARD_COMPOSE_PROJECT") != project
            or os.environ.get("RUNGUARD_PUBLIC_MODE") in {"1", "true"}):
        raise DemoDenied("Local demo mode and a dedicated rg-demo-* project are required")


class DemoControl:
    def __init__(self, database_url: str | None = None):
        require_demo_mode()
        self.users = UserRepository(database_url)
        path = Path(self.users.database_url.removeprefix("sqlite:///"))
        if path == Path(":memory:"):
            raise DemoDenied("Demo requires a persistent SQLite file")
        self.state_path = Path(f"{path.resolve()}.demo-state.json")
        self.control_path = Path(f"{path.resolve()}.demo-control.lock")

    def close(self) -> None:
        self.users.close()

    def state(self) -> dict | None:
        try:
            return json.loads(self.state_path.read_text())
        except FileNotFoundError:
            return None

    def _save(self, state: dict | None) -> None:
        if state is None:
            self.state_path.unlink(missing_ok=True)
            return
        temporary = self.state_path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as file:
            json.dump(state, file, separators=(",", ":"))
        os.replace(temporary, self.state_path)

    def _locked(self, operation):
        self.control_path.parent.mkdir(parents=True, exist_ok=True)
        with self.control_path.open("a+") as file:
            fcntl.flock(file, fcntl.LOCK_EX)
            try:
                return operation()
            finally:
                fcntl.flock(file, fcntl.LOCK_UN)

    def pause(self) -> dict:
        def operation():
            current = self.state()
            if current is not None:
                if current["status"] == "paused":
                    return current
                raise DemoDenied("Finish or clean up the previous demo run first")
            with self.users.sessions() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                active = session.scalars(select(RunRecord).where(
                    RunRecord.status.in_(("queued", "running")))).all()
                if active:
                    raise DemoDenied("Unrelated queued or running jobs exist in this volume")
                self._save({"status": "paused", "run_id": None, "owner_id": None})
                session.commit()
            return self.state()
        return self._locked(operation)

    def bind(self, run_id: str) -> dict:
        try:
            run_id = str(UUID(run_id))
        except ValueError:
            raise DemoDenied("A fixture run UUID is required") from None

        def operation():
            current = self.state()
            if current is None or current["status"] != "paused":
                raise DemoDenied("Pause the isolated demo worker first")
            if current["run_id"] is not None:
                if current["run_id"] == run_id:
                    return current
                raise DemoDenied("A different fixture run is already bound")
            with self.users.sessions() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                active = session.scalars(select(RunRecord).where(
                    RunRecord.status.in_(("queued", "running")))).all()
                if len(active) != 1 or active[0].id != run_id or active[0].status != "queued":
                    raise DemoDenied("Only the single queued fixture run may be bound")
                if active[0].kind != "website_monitor":
                    raise DemoDenied("The fixture must be a website monitor")
                current.update(run_id=run_id, owner_id=active[0].owner_user_id)
                session.add(new_run_log(run_id, active[0].owner_user_id, now(), "WARN",
                                        "DEMO_WORKER_PAUSED"))
                self._save(current)
                session.commit()
            return current
        return self._locked(operation)

    def resume(self) -> dict:
        def operation():
            current = self.state()
            if current is None or current["run_id"] is None:
                raise DemoDenied("Bind one fixture run before resuming")
            if current["status"] == "resumed":
                return current
            with self.users.sessions() as session:
                session.execute(text("BEGIN IMMEDIATE"))
                active = session.scalars(select(RunRecord).where(
                    RunRecord.status.in_(("queued", "running")))).all()
                if len(active) != 1 or active[0].id != current["run_id"]:
                    raise DemoDenied("Unrelated queued or running jobs block demo resume")
                current["status"] = "resumed"
                session.add(new_run_log(current["run_id"], current["owner_id"], now(), "INFO",
                                        "DEMO_WORKER_RESUMED"))
                self._save(current)
                session.commit()
            return current
        return self._locked(operation)

    def cleanup(self) -> dict | None:
        def operation():
            current = self.state()
            if current is None:
                return None
            if current["run_id"] is None:
                self._save(None)
                return None
            with self.users.sessions() as session:
                run = session.get(RunRecord, current["run_id"])
                if run is not None and run.status in {"queued", "running"}:
                    # Release the pause even after an interrupted smoke; the
                    # bounded demo worker can now finish only this run.
                    current["status"] = "resumed"
                    self._save(current)
                    return current
            self._save(None)
            return None
        return self._locked(operation)


def fake_monitor(snapshot):
    return monitor(snapshot, fetch=lambda _url: FetchResponse(200, {}, b""))


class DemoWorkerRunner(WorkerRunner):
    def __init__(self, database_url: str | None = None):
        require_demo_mode()
        super().__init__(database_url, handlers={"website_monitor": fake_monitor})
        self.control = DemoControl(self.db.database_url)

    def __exit__(self, *exc):
        self.control.close()
        super().__exit__(*exc)

    def claim_oldest(self):
        state = self.control.state()
        if state is None or state["status"] != "resumed" or state["run_id"] is None:
            return None
        return super().claim_oldest(allowed_run_id=state["run_id"])

    def run_once(self) -> bool:
        state = self.control.state()
        if state is None or state["status"] == "paused":
            return False
        with self.db.sessions() as session:
            active = session.scalars(select(RunRecord).where(
                RunRecord.status.in_(("queued", "running")))).all()
            if len(active) != 1 or active[0].id != state["run_id"]:
                self._touch_heartbeat()
                return False
        return super().run_once()

    def poll(self, interval_seconds: float = 1.0) -> None:
        while True:
            if not self.run_once():
                time.sleep(interval_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="RunGuard local demo worker control")
    parser.add_argument("command", choices=("serve", "pause", "bind", "resume", "cleanup", "status"))
    parser.add_argument("run_id", nargs="?")
    args = parser.parse_args()
    try:
        require_demo_mode()
        if args.command == "serve":
            with DemoWorkerRunner() as runner:
                runner.poll()
        else:
            control = DemoControl()
            try:
                if args.command == "bind":
                    result = control.bind(args.run_id or "")
                else:
                    result = getattr(control, args.command)()
                # IDs and the local state only. Never print account or URL data.
                print(json.dumps(result or {"status": "idle"}, separators=(",", ":")))
            finally:
                control.close()
    except DemoDenied as error:
        parser.exit(2, f"Demo control denied: {error}\n")
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
