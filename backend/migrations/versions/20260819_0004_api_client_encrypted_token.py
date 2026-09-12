"""Store recoverable script API tokens for the management interface.

Revision ID: 20260819_0004
Revises: 20260818_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260819_0004"
down_revision: str | None = "20260818_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("api_clients", sa.Column("token_nonce", sa.LargeBinary(), nullable=True))
    op.add_column("api_clients", sa.Column("token_ciphertext", sa.LargeBinary(), nullable=True))
    op.add_column("api_clients", sa.Column("token_key_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("api_clients") as batch_op:
        batch_op.drop_column("token_key_version")
        batch_op.drop_column("token_ciphertext")
        batch_op.drop_column("token_nonce")
