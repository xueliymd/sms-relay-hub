"""Add immutable receiving identity snapshots and message read state.

Revision ID: 20260818_0003
Revises: 20260817_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260818_0003"
down_revision: str | None = "20260817_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("receiving_phone_lookup_hash", sa.String(length=64), nullable=True),
    )
    op.add_column("messages", sa.Column("receiving_phone_nonce", sa.LargeBinary(), nullable=True))
    op.add_column(
        "messages", sa.Column("receiving_phone_ciphertext", sa.LargeBinary(), nullable=True)
    )
    op.add_column(
        "messages", sa.Column("receiving_phone_key_version", sa.Integer(), nullable=True)
    )
    op.add_column(
        "messages",
        sa.Column(
            "receiving_identity_status",
            sa.String(length=16),
            nullable=False,
            server_default="unresolved",
        ),
    )
    op.add_column("messages", sa.Column("read_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_messages_receiving_phone_lookup_hash",
        "messages",
        ["receiving_phone_lookup_hash"],
    )
    op.create_index(
        "ix_messages_receiving_identity_status",
        "messages",
        ["receiving_identity_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_messages_receiving_identity_status", table_name="messages")
    op.drop_index("ix_messages_receiving_phone_lookup_hash", table_name="messages")
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("read_at")
        batch_op.drop_column("receiving_identity_status")
        batch_op.drop_column("receiving_phone_key_version")
        batch_op.drop_column("receiving_phone_ciphertext")
        batch_op.drop_column("receiving_phone_nonce")
        batch_op.drop_column("receiving_phone_lookup_hash")
