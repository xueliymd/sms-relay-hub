"""Create the initial relay schema.

Revision ID: 20260816_0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("client_type", sa.String(length=32), nullable=False),
        sa.Column("auth_kind", sa.String(length=16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("credential_hash", sa.String(length=64), nullable=True),
        sa.Column("hmac_secret_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("hmac_secret_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("hmac_secret_key_version", sa.Integer(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_devices_active", "devices", ["active"])
    op.create_index("ix_devices_client_type", "devices", ["client_type"])
    op.create_index("ix_devices_credential_hash", "devices", ["credential_hash"])

    op.create_table(
        "api_clients",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_api_clients_active", "api_clients", ["active"])
    op.create_index("ix_api_clients_token_hash", "api_clients", ["token_hash"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_index("ix_audit_events_entity_id", "audit_events", ["entity_id"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])

    op.create_table(
        "device_nonces",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "device_id",
            sa.String(length=36),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("nonce", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("device_id", "nonce", name="uq_device_nonce"),
    )
    op.create_index("ix_device_nonces_created_at", "device_nonces", ["created_at"])
    op.create_index("ix_device_nonces_device_id", "device_nonces", ["device_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("device_id", sa.String(length=36), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("client_message_id", sa.String(length=200), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sender_summary", sa.String(length=200), nullable=False),
        sa.Column("sender_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("sender_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("sender_key_version", sa.Integer(), nullable=True),
        sa.Column("body_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("body_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("body_key_version", sa.Integer(), nullable=True),
        sa.Column("metadata_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("metadata_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("metadata_key_version", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("sim_slot", sa.Integer(), nullable=True),
        sa.Column("redacted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("device_id", "client_message_id", name="uq_device_client_message"),
    )
    op.create_index("ix_messages_created_at", "messages", ["created_at"])
    op.create_index("ix_messages_device_id", "messages", ["device_id"])
    op.create_index("ix_messages_received_at", "messages", ["received_at"])
    op.create_index("ix_messages_redacted", "messages", ["redacted"])
    op.create_index("ix_messages_source", "messages", ["source"])

    op.create_table(
        "otps",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "message_id",
            sa.String(length=36),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("code_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("code_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("code_key_version", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_otps_created_at", "otps", ["created_at"])
    op.create_index("ix_otps_expires_at", "otps", ["expires_at"])
    op.create_index("ix_otps_message_id", "otps", ["message_id"])
    op.create_index("ix_otps_status", "otps", ["status"])


def downgrade() -> None:
    op.drop_table("otps")
    op.drop_table("messages")
    op.drop_table("device_nonces")
    op.drop_table("audit_events")
    op.drop_table("api_clients")
    op.drop_table("devices")
