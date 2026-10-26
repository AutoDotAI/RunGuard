"""Store the PostgreSQL worker heartbeat in shared database state.

Revision ID: 0007_worker_heartbeat
Revises: 0006_incident_analysis
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_worker_heartbeat"
down_revision = "0006_incident_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeat",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("last_seen_at", sa.String(35), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeat")
