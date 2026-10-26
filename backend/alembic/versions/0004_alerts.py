"""Persist owner-scoped user-impact alerts.

Revision ID: 0004_alerts
Revises: 0003_active_run_index
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_alerts"
down_revision = "0003_active_run_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("symptom", sa.String(256), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(35), nullable=False),
        sa.Column("resolved_at", sa.String(35), nullable=True),
    )
    op.create_index("ix_alerts_owner_created", "alerts", ["owner_user_id", "created_at"])
    op.create_index("uq_alerts_open_fingerprint", "alerts", ["fingerprint"],
                    unique=True, sqlite_where=sa.text("status = 'open'"))


def downgrade() -> None:
    op.drop_index("uq_alerts_open_fingerprint", table_name="alerts")
    op.drop_index("ix_alerts_owner_created", table_name="alerts")
    op.drop_table("alerts")
