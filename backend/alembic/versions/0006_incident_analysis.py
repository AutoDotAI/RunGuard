"""Audit optional host-side CLI investigation attempts.

Revision ID: 0006_incident_analysis
Revises: 0005_responder
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_incident_analysis"
down_revision = "0005_responder"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incident_analyses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("incident_id", sa.String(36), sa.ForeignKey("responder_incidents.id"), nullable=False),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("failure_code", sa.String(40)),
        sa.Column("evidence_ids_json", sa.Text(), nullable=False),
        sa.Column("advisory", sa.Text()),
        sa.Column("content_digest", sa.String(64)),
        sa.Column("started_at", sa.String(35), nullable=False),
        sa.Column("finished_at", sa.String(35)),
        sa.UniqueConstraint("incident_id", "attempt", name="uq_incident_analysis_attempt"),
    )


def downgrade() -> None:
    op.drop_table("incident_analyses")
