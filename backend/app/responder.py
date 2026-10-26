"""Offline, fixed-policy response to persisted alerts.

This process has no HTTP route, model tools, shell execution or job retry path.
Untrusted run text is never used to select a tool or authorize an action.
"""

import argparse
import json
import time
from uuid import uuid4

from sqlalchemy import func, select

from .alerting import AlertService
from .db import (AlertRecord, IncidentRecord, ResponderActionRecord,
                 RunLogRecord, RunRecord, UserRepository)
from .db_serialization import serialize
from .worker_health import heartbeat_age
from .demo_worker import DemoControl, DemoDenied, require_demo_mode
from .run_logs import MESSAGES
from .store import now


POLICY = "fixed_responder_v1"
INSPECTIONS = ("get_worker_health", "get_failed_run", "get_recent_errors", "get_queue_depth")


class Responder:
    def __init__(self, users: UserRepository, *, recovery_timeout: float = 35,
                 sleep=time.sleep, clock=time.monotonic):
        self.users = users
        self.recovery_timeout = recovery_timeout
        self.sleep = sleep
        self.clock = clock

    def get_worker_health(self) -> dict:
        age = heartbeat_age(self.users)
        return {"healthy": age is not None and age <= 15, "heartbeat_age_seconds": age}

    @staticmethod
    def get_failed_run(session, owner: str, run_id: str) -> dict | None:
        run = session.scalar(select(RunRecord).where(RunRecord.id == run_id,
                                                     RunRecord.owner_user_id == owner))
        if run is None or run.status != "failed":
            return None
        code = run.error_code if run.error_code in MESSAGES else "HANDLER_ERROR"
        return {"run_id": run.id, "error_code": code, "status": run.status,
                "trace_id": run.trace_id}

    @staticmethod
    def get_recent_errors(session, owner: str, run_id: str) -> list[dict]:
        errors = session.scalars(select(RunLogRecord).where(
            RunLogRecord.owner_user_id == owner,
            RunLogRecord.level == "ERROR").order_by(RunLogRecord.id.desc()).limit(5)).all()
        # A stalled run has not failed yet, so it may have no ERROR log. Include
        # only its fixed lifecycle events as evidence, never log message text.
        lifecycle = session.scalars(select(RunLogRecord).where(
            RunLogRecord.owner_user_id == owner, RunLogRecord.run_id == run_id,
            RunLogRecord.event.in_(("RUN_QUEUED", "DEMO_WORKER_PAUSED",
                                     "DEMO_WORKER_RESUMED", "RUN_CLAIMED")))
            .order_by(RunLogRecord.id.desc()).limit(5)).all()
        rows = sorted({row.id: row for row in [*errors, *lifecycle]}.values(),
                      key=lambda row: row.id, reverse=True)
        return [{"log_id": row.id, "run_id": row.run_id,
                 "event": row.event if row.event in MESSAGES else "HANDLER_ERROR"}
                for row in rows]

    @staticmethod
    def get_queue_depth(session, owner: str) -> dict[str, int]:
        owned = session.scalar(select(func.count()).select_from(RunRecord).where(
            RunRecord.owner_user_id == owner, RunRecord.status == "queued")) or 0
        total_active = session.scalar(select(func.count()).select_from(RunRecord).where(
            RunRecord.status.in_(("queued", "running")))) or 0
        return {"owned_queued": owned, "total_active": total_active}

    @staticmethod
    def _public(session, incident: IncidentRecord) -> dict:
        audit = session.scalar(select(ResponderActionRecord).where(
            ResponderActionRecord.incident_id == incident.id))
        return {"incident_id": incident.id, "alert_id": incident.alert_id,
                "run_id": incident.run_id, "trace_id": incident.trace_id,
                "created_at": incident.created_at, "updated_at": incident.updated_at,
                "evidence": json.loads(incident.evidence_json), "symptom": incident.symptom,
                "likely_cause": incident.likely_cause,
                "recommendation": incident.recommendation,
                "decision_source": incident.decision_source, "status": incident.status,
                "action": ({"id": audit.id, "action": audit.action, "decision": audit.decision,
                            "reason": audit.reason, "before_status": audit.before_status,
                            "after_status": audit.after_status, "result": audit.result,
                            "created_at": audit.created_at, "updated_at": audit.updated_at}
                           if audit else None)}

    def respond(self, alert_id: str, owner_id: str) -> dict | None:
        """One incident per owned open alert; reserve an action before invoking it."""
        with self.users.sessions() as session:
            serialize(session, postgres=self.users.engine.dialect.name == "postgresql",
                      key=0x5247524553504F4E)
            alert = session.scalar(select(AlertRecord).where(
                AlertRecord.id == alert_id, AlertRecord.owner_user_id == owner_id))
            if alert is None:
                session.commit()
                return None
            run = session.scalar(select(RunRecord).where(
                RunRecord.id == alert.run_id, RunRecord.owner_user_id == owner_id))
            if run is None or alert.kind not in {"repeated_run_failure", "worker_queue_stalled"}:
                session.commit()
                return None
            existing = session.scalar(select(IncidentRecord).where(IncidentRecord.alert_id == alert.id))
            if existing is not None:
                result = self._public(session, existing)
                session.commit()
                return result
            if alert.status != "open":
                session.commit()
                return None

            health = self.get_worker_health()
            failed = self.get_failed_run(session, owner_id, run.id)
            errors = self.get_recent_errors(session, owner_id, run.id)
            depth = self.get_queue_depth(session, owner_id)
            # Evidence is IDs, fixed event codes and bounded health facts. Never
            # use alert.symptom, run.error_message, URLs, or fetched page text.
            evidence = {"inspection_names": INSPECTIONS, "alert_id": alert.id,
                        "run_id": run.id, "trace_id": run.trace_id,
                        "log_ids": [item["log_id"] for item in errors],
                        "error_run_ids": [item["run_id"] for item in errors],
                        "log_events": [item["event"] for item in errors],
                        "error_code": failed["error_code"] if failed else None,
                        "queue_depth": depth["owned_queued"], "worker_healthy": health["healthy"]}
            if alert.kind == "worker_queue_stalled":
                cause = "worker_queue_stalled"
                symptom = "A run is queued while the worker is unhealthy."
                recommendation = "Inspect the worker heartbeat and resume the isolated demo worker if authorized."
            elif failed and failed["error_code"] in {"HTTP_STATUS_ERROR", "DNS_ERROR", "FETCH_TIMEOUT",
                                                       "FETCH_ERROR", "DESTINATION_BLOCKED"}:
                cause = "target_site_failure"
                symptom = "Repeated configured-target checks failed."
                recommendation = "Inspect the target and run logs; a user may retry manually."
            else:
                cause = "job_execution_failure"
                symptom = "Repeated jobs failed in RunGuard."
                recommendation = "Inspect worker and run logs; a user may retry manually."

            decision, reason = "action_denied", "manual_retry_only"
            if alert.kind == "worker_queue_stalled":
                decision, reason = "approval_required", "outside_demo"
                try:
                    require_demo_mode()
                    control = DemoControl(self.users.database_url)
                    try:
                        state = control.state()
                    finally:
                        control.close()
                    if (state is None or state.get("status") != "paused"
                            or state.get("run_id") != run.id
                            or state.get("owner_id") != owner_id
                            or run.status != "queued"
                            or depth["owned_queued"] != 1 or depth["total_active"] != 1):
                        decision, reason = "action_denied", "demo_binding_mismatch"
                    else:
                        decision, reason = "reserved", "demo_gate_passed"
                except DemoDenied:
                    pass

            timestamp = now()
            incident = IncidentRecord(id=str(uuid4()), alert_id=alert.id,
                                      owner_user_id=owner_id, run_id=run.id,
                                      trace_id=run.trace_id, evidence_json=json.dumps(evidence),
                                      symptom=symptom, likely_cause=cause,
                                      recommendation=recommendation,
                                      decision_source=POLICY, status="diagnosed",
                                      created_at=timestamp, updated_at=timestamp)
            audit = ResponderActionRecord(
                id=str(uuid4()), incident_id=incident.id, alert_id=alert.id,
                actor="local_responder", policy=POLICY,
                action="resume_demo_worker" if alert.kind == "worker_queue_stalled" else "none",
                decision=decision, reason=reason, before_status=run.status,
                after_status=None, result="not_run", created_at=timestamp, updated_at=timestamp)
            session.add_all([incident, audit])
            session.commit()
            incident_id, audit_id = incident.id, audit.id

        if decision == "reserved":
            self._resume_and_verify(incident_id, audit_id, run.id)
        with self.users.sessions() as session:
            return self._public(session, session.get(IncidentRecord, incident_id))

    def _resume_and_verify(self, incident_id: str, audit_id: str, run_id: str) -> None:
        try:
            control = DemoControl(self.users.database_url)
            try:
                state = control.resume(require_paused=True)
            finally:
                control.close()
            if state.get("run_id") != run_id or state.get("status") != "resumed":
                raise DemoDenied("Demo binding changed")
        except DemoDenied:
            with self.users.sessions() as session:
                run = session.get(RunRecord, run_id)
                status = run.status if run else None
            self._record_outcome(incident_id, audit_id, "action_denied", "demo_gate_changed",
                                 "denied", status)
            return
        deadline = self.clock() + self.recovery_timeout
        while self.clock() < deadline:
            AlertService(self.users).evaluate()
            with self.users.sessions() as session:
                incident = session.get(IncidentRecord, incident_id)
                run = session.get(RunRecord, run_id)
                alert = session.get(AlertRecord, incident.alert_id)
                if run and run.status not in {"queued", "running"} and alert and alert.status == "resolved":
                    self._record_outcome(incident_id, audit_id, "executed", "demo_gate_passed",
                                         "recovered" if run.status == "succeeded" else "run_failed",
                                         run.status)
                    return
            self.sleep(0.5)
        with self.users.sessions() as session:
            run = session.get(RunRecord, run_id)
            status = run.status if run else None
        self._record_outcome(incident_id, audit_id, "executed", "demo_gate_passed",
                             "recovery_timeout", status)

    def _record_outcome(self, incident_id: str, audit_id: str, decision: str,
                        reason: str, result: str, after_status: str | None = None) -> None:
        with self.users.sessions() as session:
            serialize(session, postgres=self.users.engine.dialect.name == "postgresql",
                      key=0x5247524553504F4E)
            incident = session.get(IncidentRecord, incident_id)
            audit = session.get(ResponderActionRecord, audit_id)
            incident.status = "recovered" if result == "recovered" else "needs_review"
            incident.updated_at = now()
            audit.decision, audit.reason, audit.result = decision, reason, result
            audit.after_status = after_status
            audit.updated_at = now()
            session.commit()

    def run_open_once(self) -> list[dict]:
        """Scan only persisted open alerts; no alert means no incident or action."""
        with self.users.sessions() as session:
            pending = session.execute(select(AlertRecord.id, AlertRecord.owner_user_id).where(
                AlertRecord.status == "open").limit(1000)).all()
        return [result for alert_id, owner in pending
                if (result := self.respond(alert_id, owner)) is not None]


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline RunGuard alert responder")
    parser.add_argument("command", choices=("once", "serve", "inspect"))
    parser.add_argument("alert_id", nargs="?")
    parser.add_argument("owner_id", nargs="?")
    args = parser.parse_args()
    users = UserRepository()
    users.migrate()
    try:
        responder = Responder(users)
        if args.command == "inspect":
            if not args.alert_id or not args.owner_id:
                parser.error("inspect requires alert_id and owner_id")
            result = responder.respond(args.alert_id, args.owner_id)
            print(json.dumps(result or {"status": "no_open_owned_alert"}, separators=(",", ":")))
        elif args.command == "once":
            print(json.dumps(responder.run_open_once(), separators=(",", ":")))
        else:
            while True:
                responder.run_open_once()
                time.sleep(5)
    except KeyboardInterrupt:
        return 0
    finally:
        users.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
