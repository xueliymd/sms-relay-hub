"""Link managed Air724 consumer devices to their script API token.

Revision ID: 20260823_0005
Revises: 20260819_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260823_0005"
down_revision: str | None = "20260819_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("api_clients") as batch_op:
        batch_op.add_column(sa.Column("device_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_api_clients_device_id_devices",
            "devices",
            ["device_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index("ix_api_clients_device_id", ["device_id"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("api_clients") as batch_op:
        batch_op.drop_index("ix_api_clients_device_id")
        batch_op.drop_constraint("fk_api_clients_device_id_devices", type_="foreignkey")
        batch_op.drop_column("device_id")
