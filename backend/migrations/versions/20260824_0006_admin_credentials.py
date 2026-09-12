"""Add administrator username and password credentials.

Revision ID: 20260824_0006
Revises: 20260823_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_0006"
down_revision: str | None = "20260823_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_accounts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_admin_accounts_username",
        "admin_accounts",
        ["username"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_admin_accounts_username", table_name="admin_accounts")
    op.drop_table("admin_accounts")
