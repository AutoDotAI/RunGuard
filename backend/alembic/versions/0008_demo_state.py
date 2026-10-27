"""Persist the isolated demo control state across PostgreSQL processes.

Revision ID: 0008_demo_state
Revises: 0007_worker_heartbeat
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_demo_state"
down_revision = "0007_worker_heartbeat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "demo_control_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("state_json", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("demo_control_state")
