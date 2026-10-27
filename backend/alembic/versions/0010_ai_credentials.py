"""Persist account-owned, encrypted provider API credentials.

Revision ID: 0010_ai_credentials
Revises: 0009_ai_settings
"""

from alembic import op
import sqlalchemy as sa


revision = "0010_ai_credentials"
down_revision = "0009_ai_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_credentials",
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("provider", sa.String(16), primary_key=True),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.String(35), nullable=False),
        sa.CheckConstraint("provider IN ('openai', 'anthropic')", name="ck_ai_credentials_provider"),
    )


def downgrade() -> None:
    op.drop_table("ai_credentials")
