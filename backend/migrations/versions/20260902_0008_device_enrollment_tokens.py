"""Add short-lived one-time device enrollment tokens.

Revision ID: 20260902_0008
Revises: 20260901_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_0008"
down_revision: str | None = "20260901_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_enrollment_tokens",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("device_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_device_enrollment_token_hash"),
    )
    op.create_index(
        "ix_device_enrollment_tokens_device_id",
        "device_enrollment_tokens",
        ["device_id"],
        unique=False,
    )
    op.create_index(
        "ix_device_enrollment_tokens_token_hash",
        "device_enrollment_tokens",
        ["token_hash"],
        unique=False,
    )
    op.create_index(
        "ix_device_enrollment_tokens_expires_at",
        "device_enrollment_tokens",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_device_enrollment_tokens_created_at",
        "device_enrollment_tokens",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_device_enrollment_tokens_created_at", table_name="device_enrollment_tokens")
    op.drop_index("ix_device_enrollment_tokens_expires_at", table_name="device_enrollment_tokens")
    op.drop_index("ix_device_enrollment_tokens_token_hash", table_name="device_enrollment_tokens")
    op.drop_index("ix_device_enrollment_tokens_device_id", table_name="device_enrollment_tokens")
    op.drop_table("device_enrollment_tokens")
