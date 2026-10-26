"""Create persistent worker, run, and log tables.

Revision ID: 0002_app_data
Revises: 0001_users
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_app_data"
down_revision = "0001_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(35), nullable=False),
        sa.Column("updated_at", sa.String(35), nullable=False),
        sa.Column("archived_at", sa.String(35), nullable=True),
    )
    op.create_index("ix_workers_owner_archived", "workers", ["owner_user_id", "archived_at"])
    op.create_table(
        "runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("worker_id", sa.String(36), sa.ForeignKey("workers.id"), nullable=False),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("worker_name_snapshot", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("url_snapshot", sa.String(2048), nullable=False),
        sa.Column("config_snapshot_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.String(35), nullable=False),
        sa.Column("started_at", sa.String(35), nullable=True),
        sa.Column("finished_at", sa.String(35), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_of_run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("traceparent", sa.String(128), nullable=True),
    )
    op.create_index("ix_runs_owner_created", "runs", ["owner_user_id", "created_at"])
    op.create_index("ix_runs_worker_created", "runs", ["worker_id", "created_at"])
    op.create_index("ix_runs_worker_status", "runs", ["worker_id", "status"])
    op.create_table(
        "run_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("timestamp", sa.String(35), nullable=False),
        sa.Column("level", sa.String(8), nullable=False),
        sa.Column("event", sa.String(80), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
    )
    op.create_index("ix_run_logs_run_id", "run_logs", ["run_id", "id"])
    op.create_index("ix_run_logs_owner_id", "run_logs", ["owner_user_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_run_logs_owner_id", table_name="run_logs")
    op.drop_index("ix_run_logs_run_id", table_name="run_logs")
    op.drop_table("run_logs")
    op.drop_index("ix_runs_worker_status", table_name="runs")
    op.drop_index("ix_runs_worker_created", table_name="runs")
    op.drop_index("ix_runs_owner_created", table_name="runs")
    op.drop_table("runs")
    op.drop_index("ix_workers_owner_archived", table_name="workers")
    op.drop_table("workers")
