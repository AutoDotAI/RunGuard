"""Enforce one queued or running run per worker in SQLite.

Revision ID: 0003_active_run_index
Revises: 0002_app_data
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_active_run_index"
down_revision = "0002_app_data"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_runs_one_active_per_worker", "runs", ["worker_id"], unique=True,
        sqlite_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_runs_one_active_per_worker", table_name="runs")
