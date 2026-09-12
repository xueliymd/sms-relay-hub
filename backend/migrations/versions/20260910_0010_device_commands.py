"""Add device commands for remote control (query battery / recent SMS / trigger upload).

Revision ID: 20260910_0010
Revises: 20260910_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0010"
down_revision: str | None = "20260910_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_commands",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "device_id",
            sa.String(length=36),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("command_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("result_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("result_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("result_key_version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_device_commands_device_id", "device_commands", ["device_id"])
    op.create_index("ix_device_commands_status", "device_commands", ["status"])
    op.create_index("ix_device_commands_created_at", "device_commands", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_device_commands_created_at", table_name="device_commands")
    op.drop_index("ix_device_commands_status", table_name="device_commands")
    op.drop_index("ix_device_commands_device_id", table_name="device_commands")
    op.drop_table("device_commands")
