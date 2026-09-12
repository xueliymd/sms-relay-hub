from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AdminAccountModel(Base):
    __tablename__ = "admin_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    session_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class DeviceModel(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    client_type: Mapped[str] = mapped_column(String(32), index=True)
    auth_kind: Mapped[str] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    credential_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    hmac_secret_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    hmac_secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    hmac_secret_key_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    client_instance_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    client_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_source_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    runtime_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    credential_reuse_warning: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    duplicate_collection_warning: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class DeviceNonceModel(Base):
    __tablename__ = "device_nonces"
    __table_args__ = (UniqueConstraint("device_id", "nonce", name="uq_device_nonce"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    nonce: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class DeviceEnrollmentTokenModel(Base):
    __tablename__ = "device_enrollment_tokens"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_device_enrollment_token_hash"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class DeviceSimProfileModel(Base):
    __tablename__ = "device_sim_profiles"
    __table_args__ = (
        CheckConstraint("source IN ('auto', 'manual', 'vendor')", name="ck_sim_profile_source"),
    )

    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    slot_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    phone_number_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    phone_number_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    phone_number_key_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    carrier_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class MessageModel(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("device_id", "client_message_id", name="uq_device_client_message"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    client_message_id: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    sender_summary: Mapped[str] = mapped_column(String(200))
    sender_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    sender_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    sender_key_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    body_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    body_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    body_key_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    metadata_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    metadata_key_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    sim_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    receiving_phone_lookup_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    receiving_phone_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    receiving_phone_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    receiving_phone_key_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    receiving_identity_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unresolved", index=True
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    redacted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    sender_body_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)


class OtpModel(Base):
    __tablename__ = "otps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), unique=True, index=True
    )
    code_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    code_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    code_key_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="available", index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ApiClientModel(Base):
    __tablename__ = "api_clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_id: Mapped[str | None] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=True, unique=True, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    token_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    token_key_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuditEventModel(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    details: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class ForwardingRuleModel(Base):
    __tablename__ = "forwarding_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    match_device_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    match_phone_lookup_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    match_sender: Mapped[str | None] = mapped_column(String(120), nullable=True)
    match_keyword: Mapped[str | None] = mapped_column(String(200), nullable=True)
    channel: Mapped[str] = mapped_column(String(16))
    target_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    target_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    target_key_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    template: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ForwardingLogModel(Base):
    __tablename__ = "forwarding_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("forwarding_rules.id", ondelete="CASCADE"), nullable=True, index=True
    )
    rule_name: Mapped[str] = mapped_column(String(120))
    message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    channel: Mapped[str] = mapped_column(String(16))
    target_masked: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class DeviceCommandModel(Base):
    __tablename__ = "device_commands"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    command_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    result_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    result_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    result_key_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
