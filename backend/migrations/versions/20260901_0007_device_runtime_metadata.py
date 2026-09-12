"""Add device runtime metadata and conservative conflict warnings.

Revision ID: 20260901_0007
Revises: 20260824_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0007"
down_revision: str | None = "20260824_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch_op:
        batch_op.add_column(sa.Column("client_instance_id", sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column("client_version", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("client_mode", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column("last_source_fingerprint", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(sa.Column("runtime_updated_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column(
                "credential_reuse_warning",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "duplicate_collection_warning",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch_op:
        batch_op.drop_column("duplicate_collection_warning")
        batch_op.drop_column("credential_reuse_warning")
        batch_op.drop_column("runtime_updated_at")
        batch_op.drop_column("last_source_fingerprint")
        batch_op.drop_column("client_mode")
        batch_op.drop_column("client_version")
        batch_op.drop_column("client_instance_id")
