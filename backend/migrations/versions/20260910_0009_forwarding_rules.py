"""Add forwarding rules engine tables and message sender/body digest.

Revision ID: 20260910_0009
Revises: 20260902_0008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0009"
down_revision: str | None = "20260902_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(sa.Column("sender_body_digest", sa.String(length=64), nullable=True))

    op.create_table(
        "forwarding_rules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("match_device_id", sa.String(length=36), nullable=True),
        sa.Column("match_phone_lookup_hash", sa.String(length=64), nullable=True),
        sa.Column("match_sender", sa.String(length=120), nullable=True),
        sa.Column("match_keyword", sa.String(length=200), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("target_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("target_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("target_key_version", sa.Integer(), nullable=True),
        sa.Column("template", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_forwarding_rules_enabled", "forwarding_rules", ["enabled"])

    op.create_table(
        "forwarding_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "rule_id",
            sa.String(length=36),
            sa.ForeignKey("forwarding_rules.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("rule_name", sa.String(length=120), nullable=False),
        sa.Column("message_id", sa.String(length=36), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("target_masked", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_forwarding_logs_created_at", "forwarding_logs", ["created_at"])
    op.create_index("ix_forwarding_logs_status", "forwarding_logs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_forwarding_logs_status", table_name="forwarding_logs")
    op.drop_index("ix_forwarding_logs_created_at", table_name="forwarding_logs")
    op.drop_table("forwarding_logs")
    op.drop_index("ix_forwarding_rules_enabled", table_name="forwarding_rules")
    op.drop_table("forwarding_rules")
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("sender_body_digest")
