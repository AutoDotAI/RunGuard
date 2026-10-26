"""Persist one responder incident and bounded action per alert.

Revision ID: 0005_responder
Revises: 0004_alerts
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_responder"
down_revision = "0004_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "responder_incidents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("alert_id", sa.String(36), sa.ForeignKey("alerts.id"), unique=True, nullable=False),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("trace_id", sa.String(64)),
        sa.Column("evidence_json", sa.Text(), nullable=False),
        sa.Column("symptom", sa.String(256), nullable=False),
        sa.Column("likely_cause", sa.String(80), nullable=False),
        sa.Column("recommendation", sa.String(256), nullable=False),
        sa.Column("decision_source", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.String(35), nullable=False),
        sa.Column("updated_at", sa.String(35), nullable=False),
    )
    op.create_table(
        "responder_actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("incident_id", sa.String(36), sa.ForeignKey("responder_incidents.id"), unique=True, nullable=False),
        sa.Column("alert_id", sa.String(36), sa.ForeignKey("alerts.id"), nullable=False),
        sa.Column("actor", sa.String(32), nullable=False),
        sa.Column("policy", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(80), nullable=False),
        sa.Column("before_status", sa.String(16), nullable=False),
        sa.Column("after_status", sa.String(16)),
        sa.Column("result", sa.String(80), nullable=False),
        sa.Column("created_at", sa.String(35), nullable=False),
        sa.Column("updated_at", sa.String(35), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("responder_actions")
    op.drop_table("responder_incidents")
