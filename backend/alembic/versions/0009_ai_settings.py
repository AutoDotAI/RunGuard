"""Persist one AI provider and model selection per account.

Revision ID: 0009_ai_settings
Revises: 0008_demo_state
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_ai_settings"
down_revision = "0008_demo_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_settings",
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("provider_path", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("updated_at", sa.String(35), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ai_settings")
