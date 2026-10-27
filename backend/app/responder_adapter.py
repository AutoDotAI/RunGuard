"""RunGuard persistence and demo gate for the separately packaged responder.

The fixed diagnosis/action policy and CLI live in AutoDotAI/incident-response.
This module supplies only owned database reads, atomic audit writes, and the
existing local demo control. The normal API and job worker never import it.
"""

import json
from uuid import uuid4

from incident_response.policy import (POLICY, Alert, IncidentDraft, QueueDepth,
                                      RecoveryState, Reservation, Responder)
from sqlalchemy import func, select

from .alerting import AlertService
from .db import (AlertRecord, IncidentRecord, ResponderActionRecord,
                 RunLogRecord, RunRecord, UserRepository)
from .db_serialization import ALERT_INCIDENT_LOCK, serialize
from .demo_worker import DemoControl, DemoDenied, require_demo_mode
from .store import now
from .worker_health import heartbeat_age


class RunGuardAdapter:
    def __init__(self, users: UserRepository):
        self.users = users

    def close(self) -> None:
        self.users.close()

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
                "action": ({"id": audit.id, "action": audit.action,
                            "decision": audit.decision, "reason": audit.reason,
                            "before_status": audit.before_status,
                            "after_status": audit.after_status, "result": audit.result,
                            "created_at": audit.created_at, "updated_at": audit.updated_at}
                           if audit else None)}

    def open_alerts(self) -> list[tuple[str, str]]:
        with self.users.sessions() as session:
            return list(session.execute(select(AlertRecord.id, AlertRecord.owner_user_id)
                                        .where(AlertRecord.status == "open").limit(1000)).all())

    def get_owned_alert(self, alert_id: str, owner_id: str) -> Alert | None:
        with self.users.sessions() as session:
            alert = session.scalar(select(AlertRecord).where(
                AlertRecord.id == alert_id, AlertRecord.owner_user_id == owner_id))
            if alert is None:
                return None
            run = session.scalar(select(RunRecord).where(
                RunRecord.id == alert.run_id, RunRecord.owner_user_id == owner_id))
            if run is None:
                return None
            return Alert(id=alert.id, owner_id=alert.owner_user_id,
                         run_id=run.id, run_owner_id=run.owner_user_id,
                         kind=alert.kind, status=alert.status,
                         run_status=run.status, trace_id=run.trace_id)

    def get_incident(self, alert_id: str, owner_id: str) -> dict | None:
        with self.users.sessions() as session:
            incident = session.scalar(select(IncidentRecord).where(
                IncidentRecord.alert_id == alert_id,
                IncidentRecord.owner_user_id == owner_id))
            return self._public(session, incident) if incident else None

    def get_worker_health(self) -> dict:
        age = heartbeat_age(self.users)
        return {"healthy": age is not None and age <= 15,
                "heartbeat_age_seconds": age}

    def get_failed_run(self, owner_id: str, run_id: str) -> dict | None:
        with self.users.sessions() as session:
            run = session.scalar(select(RunRecord).where(
                RunRecord.id == run_id, RunRecord.owner_user_id == owner_id))
            if run is None or run.status != "failed":
                return None
            return {"run_id": run.id, "error_code": run.error_code,
                    "status": run.status, "trace_id": run.trace_id}

    def get_recent_errors(self, owner_id: str, run_id: str) -> list[dict]:
        with self.users.sessions() as session:
            errors = session.scalars(select(RunLogRecord).where(
                RunLogRecord.owner_user_id == owner_id,
                RunLogRecord.level == "ERROR").order_by(RunLogRecord.id.desc()).limit(5)).all()
            lifecycle = session.scalars(select(RunLogRecord).where(
                RunLogRecord.owner_user_id == owner_id, RunLogRecord.run_id == run_id,
                RunLogRecord.event.in_(("RUN_QUEUED", "DEMO_WORKER_PAUSED",
                                       "DEMO_WORKER_RESUMED", "RUN_CLAIMED")))
                .order_by(RunLogRecord.id.desc()).limit(5)).all()
            rows = sorted({row.id: row for row in [*errors, *lifecycle]}.values(),
                          key=lambda row: row.id, reverse=True)
            # The external policy receives IDs and fixed event codes, never log text.
            return [{"log_id": row.id, "run_id": row.run_id, "event": row.event}
                    for row in rows]

    def get_queue_depth(self, owner_id: str) -> QueueDepth:
        with self.users.sessions() as session:
            owned = session.scalar(select(func.count()).select_from(RunRecord).where(
                RunRecord.owner_user_id == owner_id, RunRecord.status == "queued")) or 0
            total = session.scalar(select(func.count()).select_from(RunRecord).where(
                RunRecord.status.in_(("queued", "running")))) or 0
            return QueueDepth(owned_queued=owned, total_active=total)

    def demo_gate(self, alert: Alert) -> str:
        try:
            require_demo_mode()
            control = DemoControl(self.users.database_url)
            try:
                state = control.state()
            finally:
                control.close()
        except DemoDenied:
            return "outside_demo"
        if (state is None or state.get("status") != "paused"
                or state.get("run_id") != alert.run_id
                or state.get("owner_id") != alert.owner_id
                or alert.run_status != "queued"):
            return "mismatch"
        return "passed"

    def reserve_incident(self, draft: IncidentDraft) -> Reservation:
        with self.users.sessions() as session:
            serialize(session, postgres=self.users.engine.dialect.name == "postgresql",
                      key=ALERT_INCIDENT_LOCK)
            existing = session.scalar(select(IncidentRecord).where(
                IncidentRecord.alert_id == draft.alert_id))
            if existing is not None:
                result = Reservation(incident_id=existing.id, created=False,
                                     decision="already_recorded")
                session.commit()
                return result
            alert = session.scalar(select(AlertRecord).where(
                AlertRecord.id == draft.alert_id,
                AlertRecord.owner_user_id == draft.owner_id))
            run = session.scalar(select(RunRecord).where(
                RunRecord.id == draft.run_id,
                RunRecord.owner_user_id == draft.owner_id))
            if alert is None or run is None:
                raise ValueError("Owned alert and run must exist before reservation")
            decision, reason = draft.decision, draft.reason
            # The demo binding can change between the policy read and this write.
            if (decision == "reserved"
                    and (alert.status != "open" or run.status != "queued"
                         or self.demo_gate(Alert(
                             id=alert.id, owner_id=alert.owner_user_id, run_id=run.id,
                             run_owner_id=run.owner_user_id, kind=alert.kind,
                             status=alert.status, run_status=run.status,
                             trace_id=run.trace_id)) != "passed")):
                decision, reason = "action_denied", "demo_binding_mismatch"
            timestamp = now()
            incident = IncidentRecord(
                id=str(uuid4()), alert_id=alert.id, owner_user_id=draft.owner_id,
                run_id=run.id, trace_id=draft.trace_id,
                evidence_json=json.dumps(draft.evidence),
                symptom=draft.symptom, likely_cause=draft.likely_cause,
                recommendation=draft.recommendation,
                decision_source=draft.decision_source, status="diagnosed",
                created_at=timestamp, updated_at=timestamp)
            audit = ResponderActionRecord(
                id=str(uuid4()), incident_id=incident.id, alert_id=alert.id,
                actor="local_responder", policy=POLICY, action=draft.action,
                decision=decision, reason=reason, before_status=draft.before_status,
                after_status=None, result="not_run", created_at=timestamp,
                updated_at=timestamp)
            session.add_all([incident, audit])
            session.commit()
            return Reservation(incident_id=incident.id, created=True,
                               decision=decision)

    def resume_demo_worker(self, alert: Alert) -> bool:
        try:
            control = DemoControl(self.users.database_url)
            try:
                state = control.resume(require_paused=True)
            finally:
                control.close()
            return state.get("run_id") == alert.run_id and state.get("status") == "resumed"
        except DemoDenied:
            return False

    def recovery_state(self, alert: Alert) -> RecoveryState:
        AlertService(self.users).evaluate()
        with self.users.sessions() as session:
            run = session.scalar(select(RunRecord).where(
                RunRecord.id == alert.run_id,
                RunRecord.owner_user_id == alert.owner_id))
            current_alert = session.scalar(select(AlertRecord).where(
                AlertRecord.id == alert.id,
                AlertRecord.owner_user_id == alert.owner_id))
            return RecoveryState(run_status=run.status if run else None,
                                 alert_status=current_alert.status if current_alert else None)

    def record_outcome(self, incident_id: str, *, decision: str, reason: str,
                       result: str, after_status: str | None) -> None:
        with self.users.sessions() as session:
            serialize(session, postgres=self.users.engine.dialect.name == "postgresql",
                      key=ALERT_INCIDENT_LOCK)
            incident = session.get(IncidentRecord, incident_id)
            audit = session.scalar(select(ResponderActionRecord).where(
                ResponderActionRecord.incident_id == incident_id))
            if incident is None or audit is None:
                raise ValueError("Reserved incident and audit must exist")
            incident.status = "recovered" if result == "recovered" else "needs_review"
            incident.updated_at = now()
            audit.decision, audit.reason, audit.result = decision, reason, result
            audit.after_status = after_status
            audit.updated_at = now()
            session.commit()

    def read_incident(self, incident_id: str, owner_id: str) -> dict:
        with self.users.sessions() as session:
            incident = session.scalar(select(IncidentRecord).where(
                IncidentRecord.id == incident_id,
                IncidentRecord.owner_user_id == owner_id))
            if incident is None:
                raise ValueError("Owned incident not found")
            return self._public(session, incident)


class RunGuardResponder(Responder):
    """Compatibility constructor for RunGuard tests; the policy is external."""

    def __init__(self, users: UserRepository, **kwargs):
        super().__init__(RunGuardAdapter(users), **kwargs)


def make_adapter() -> RunGuardAdapter:
    """Trusted factory used by the optional responder CLI image only."""
    users = UserRepository()
    users.migrate()
    return RunGuardAdapter(users)
