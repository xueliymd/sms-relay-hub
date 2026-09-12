"""Add device SIM slot and phone mappings.

Revision ID: 20260817_0002
Revises: 20260816_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0002"
down_revision: str | None = "20260816_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_sim_profiles",
        sa.Column(
            "device_id",
            sa.String(length=36),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slot_index", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=True),
        sa.Column("phone_number_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("phone_number_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("phone_number_key_version", sa.Integer(), nullable=True),
        sa.Column("carrier_name", sa.String(length=160), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("device_id", "slot_index"),
        sa.CheckConstraint(
            "source IN ('auto', 'manual', 'vendor')", name="ck_sim_profile_source"
        ),
    )


def downgrade() -> None:
    op.drop_table("device_sim_profiles")
