import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, cast
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from sqlalchemy import Engine, and_, create_engine, delete, func, or_, select, text, update
from sqlalchemy.engine import CursorResult, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.admin_auth import (
    hash_password,
    issue_admin_session,
    parse_admin_session,
    password_needs_rehash,
    verify_password,
)
from app.core.config import settings
from app.core.crypto import (
    EncryptedValue,
    FieldCrypto,
    new_bearer,
    new_hmac_secret,
    token_hash,
    verify_hmac,
)
from app.core.redaction import mask_identifier
from app.db.models import (
    AdminAccountModel,
    ApiClientModel,
    AuditEventModel,
    Base,
    DeviceCommandModel,
    DeviceEnrollmentTokenModel,
    DeviceModel,
    DeviceNonceModel,
    DeviceSimProfileModel,
    ForwardingLogModel,
    ForwardingRuleModel,
    MessageModel,
    OtpModel,
)
from app.services.otp_parser import code_expiry, extract_code

DEVICE_CONTRACTS = {
    "android_app": ("hmac", "android_app"),
    "root_module": ("hmac", "root_module"),
    "ios_shortcut": ("bearer", "ios_shortcut"),
    "ve_tweak": ("hmac", "ve_tweak"),
    "air724_mqtt": ("hmac", "air724_mqtt"),
}

# These devices are consumers of OTP delivery, not SMS-ingest clients. Keep
# them outside DEVICE_CONTRACTS so their credentials cannot authenticate upload.
CONSUMER_DEVICE_CONTRACTS = {
    "air724": ("script_api", "air724"),
}
SUPPORTED_DEVICE_CONTRACTS = {**DEVICE_CONTRACTS, **CONSUMER_DEVICE_CONTRACTS}

# 远程控制 v1 指令白名单。设备端每类指令有独立授权开关（默认关），
# 平台侧只负责校验类型合法性，不代替设备端做授权判断。
DEVICE_COMMAND_TYPES = {
    "query_battery": "查询电量与充电状态",
    "query_recent_sms": "查询设备最近短信摘要",
    "trigger_upload": "触发设备立即上报队列",
}

# 单设备未完成指令上限，防止误点/脚本刷屏把队列堆满
DEVICE_COMMAND_QUEUE_LIMIT = 20
# 已下发但迟迟没有结果（设备取走指令后进程被杀等）的重新投递等待时长
DEVICE_COMMAND_REDELIVER_AFTER = timedelta(minutes=10)

PHONE_NUMBER_PATTERN = re.compile(r"^\+?[0-9\s().-]+$")


class InvalidDeviceCredential(Exception):
    pass


class IdempotencyConflict(Exception):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_phone_number(value: str) -> str:
    stripped = value.strip()
    if not stripped or len(value) > 64 or PHONE_NUMBER_PATTERN.fullmatch(stripped) is None:
        raise ValueError("phone_number must contain digits and supported separators")
    digits = "".join(character for character in stripped if character.isdigit())
    if not digits:
        raise ValueError("phone_number must contain digits")
    # 号码在 API、SIM 绑定、筛选、Claim 和 WebSocket 内部统一使用
    # E.164 风格。中国大陆手机号允许前端填写 11 位、86+11 位或
    # +86+11 位，避免脚本和 iOS 上报格式不同导致 WS 无法匹配。
    # Keep explicitly written +1 North-American numbers intact. Bare 11-digit
    # values beginning with 1 are treated as mainland mobile numbers.
    if stripped.startswith("+1") and len(digits) == 11:
        return "+" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+86" + digits
    if len(digits) == 13 and digits.startswith("86") and digits[2] == "1":
        return "+" + digits
    return ("+" if stripped.startswith("+") else "") + digits


@dataclass(slots=True)
class Device:
    id: UUID
    name: str
    client_type: str
    auth_kind: str
    active: bool = True
    credential_hash: str = ""
    hmac_secret: str | None = None
    last_seen_at: datetime | None = None
    client_instance_id: str | None = None
    client_version: str | None = None
    client_mode: str | None = None
    runtime_updated_at: datetime | None = None
    credential_reuse_warning: bool = False
    duplicate_collection_warning: bool = False


@dataclass(slots=True)
class Message:
    id: UUID
    device_id: UUID
    client_message_id: str
    received_at: datetime
    sender: str
    body: str
    source: str
    sim_slot: int | None
    metadata: dict[str, Any]
    receiving_phone: str | None
    receiving_identity_status: str
    receiving_phone_lookup_hash: str | None = None
    read_at: datetime | None = None
    otp_status: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    redacted: bool = False


@dataclass(slots=True)
class Otp:
    id: UUID
    message_id: UUID
    code: str
    expires_at: datetime
    status: str = "available"
    claimed_at: datetime | None = None


@dataclass(slots=True)
class DeviceSimProfile:
    device_id: UUID
    slot_index: int
    subscription_id: int | None
    phone_number: str | None
    carrier_name: str | None
    source: str
    updated_at: datetime


@dataclass(slots=True)
class DeviceEnrollmentTicket:
    device_id: UUID
    token: str
    expires_at: datetime


@dataclass(slots=True)
class DeviceEnrollmentCredential:
    device_id: UUID
    device_name: str
    client_type: str
    auth_kind: str
    hmac_secret: str


class RelayStore:
    def __init__(self) -> None:
        self.lock = RLock()
        self.engine: Engine
        self.session_factory: sessionmaker[Session]
        self.database_url = ""
        self.crypto = FieldCrypto(settings.data_encryption_key)
        self.configure(settings.database_url)

    @property
    def is_postgres(self) -> bool:
        return self.engine.dialect.name == "postgresql"

    def configure(self, database_url: str, *, drop_existing: bool = False) -> None:
        with self.lock:
            previous = getattr(self, "engine", None)
            if previous is not None:
                previous.dispose()
            kwargs: dict[str, Any] = {"pool_pre_ping": True}
            if database_url in {"sqlite://", "sqlite+pysqlite:///:memory:"}:
                kwargs.update(
                    connect_args={"check_same_thread": False},
                    poolclass=StaticPool,
                )
            elif database_url.startswith("sqlite"):
                kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
            self.engine = create_engine(database_url, **kwargs)
            self.session_factory = sessionmaker(self.engine, expire_on_commit=False)
            self.database_url = database_url
            if drop_existing:
                Base.metadata.drop_all(self.engine)
            Base.metadata.create_all(self.engine)

    def dispose(self) -> None:
        self.engine.dispose()

    def check_database(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()

    def ensure_admin_account(self) -> None:
        if not settings.admin_password:
            raise RuntimeError(
                "ADMIN_PASSWORD must be set before starting the application"
            )
        with self.lock:
            with self.session_factory.begin() as session:
                account = session.scalar(select(AdminAccountModel).limit(1))
                if account is not None:
                    return
                session.add(
                    AdminAccountModel(
                        id=str(uuid4()),
                        username=settings.admin_username,
                        password_hash=hash_password(settings.admin_password),
                        session_version=1,
                    )
                )

    def authenticate_admin(self, username: str, password: str) -> tuple[str, str, int] | None:
        with self.lock:
            with self.session_factory.begin() as session:
                account = session.scalar(
                    select(AdminAccountModel).where(AdminAccountModel.username == username)
                )
                if account is None or not verify_password(account.password_hash, password):
                    return None
                if password_needs_rehash(account.password_hash):
                    account.password_hash = hash_password(password)
                return account.id, account.username, account.session_version

    def issue_admin_session(self, account_id: str, username: str, session_version: int) -> tuple[str, int]:
        return issue_admin_session(account_id, username, session_version)

    def validate_admin_credential(self, token: str | None) -> bool:
        if token is None:
            return False
        if hmac.compare_digest(token, settings.admin_token):
            return True
        claims = parse_admin_session(token)
        if claims is None:
            return False
        with self.session_factory() as session:
            account = session.get(AdminAccountModel, claims.account_id)
            return bool(
                account
                and account.username == claims.username
                and account.session_version == claims.session_version
            )

    def update_admin_account(
        self,
        *,
        current_password: str,
        username: str | None,
        password: str | None,
    ) -> tuple[str, str, int]:
        with self.lock:
            with self.session_factory.begin() as session:
                account = session.scalar(select(AdminAccountModel).limit(1))
                if account is None or not verify_password(
                    account.password_hash, current_password
                ):
                    raise PermissionError("current password is invalid")
                if username is not None:
                    normalized = username.strip()
                    if not normalized or len(normalized) > 80:
                        raise ValueError("username must contain 1 to 80 characters")
                    duplicate = session.scalar(
                        select(AdminAccountModel).where(
                            AdminAccountModel.username == normalized,
                            AdminAccountModel.id != account.id,
                        )
                    )
                    if duplicate is not None:
                        raise ValueError("username is already in use")
                    account.username = normalized
                if password is not None:
                    if len(password) < 12 or len(password) > 256:
                        raise ValueError("password must contain 12 to 256 characters")
                    account.password_hash = hash_password(password)
                if username is None and password is None:
                    raise ValueError("username or password is required")
                account.session_version += 1
                return account.id, account.username, account.session_version

    def _audit(
        self,
        session: Session,
        event_type: str,
        entity_id: UUID | str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        session.add(
            AuditEventModel(
                event_type=event_type,
                entity_id=str(entity_id) if entity_id else None,
                details=details or {},
            )
        )

    def _begin_write_transaction(self, session: Session) -> None:
        if self.engine.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")

    def _device_for_update(self, session: Session, device_id: UUID) -> DeviceModel | None:
        self._begin_write_transaction(session)
        return session.scalar(
            select(DeviceModel)
            .where(DeviceModel.id == str(device_id))
            .with_for_update()
        )

    def audit_event(self, event: str, entity_id: UUID | None = None) -> None:
        with self.session_factory.begin() as session:
            self._audit(session, event, entity_id)

    def _device_from_model(self, model: DeviceModel, *, include_secret: bool = False) -> Device:
        secret: str | None = None
        if include_secret and model.hmac_secret_nonce and model.hmac_secret_ciphertext:
            value = EncryptedValue(
                model.hmac_secret_nonce,
                model.hmac_secret_ciphertext,
                model.hmac_secret_key_version or 1,
            )
            secret = self.crypto.decrypt_text(value, f"device:{model.id}:secret:v1")
        return Device(
            id=UUID(model.id),
            name=model.name,
            client_type=model.client_type,
            auth_kind=model.auth_kind,
            active=model.active,
            credential_hash=model.credential_hash or "",
            hmac_secret=secret,
            last_seen_at=as_utc(model.last_seen_at) if model.last_seen_at else None,
            client_instance_id=model.client_instance_id,
            client_version=model.client_version,
            client_mode=model.client_mode,
            runtime_updated_at=(
                as_utc(model.runtime_updated_at) if model.runtime_updated_at else None
            ),
            credential_reuse_warning=model.credential_reuse_warning,
            duplicate_collection_warning=model.duplicate_collection_warning,
        )

    @staticmethod
    def _runtime_metadata_text(
        metadata: dict[str, Any],
        key: str,
        *,
        max_length: int,
    ) -> str | None:
        value = metadata.get(key)
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized[:max_length] or None

    def _update_device_runtime(
        self,
        device: DeviceModel,
        *,
        payload: dict[str, Any],
        source_address: str | None,
        now: datetime,
    ) -> None:
        raw_metadata = payload.get("metadata")
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        instance_id = self._runtime_metadata_text(
            metadata, "collector_instance_id", max_length=160
        )
        client_version = self._runtime_metadata_text(
            metadata, "collector_version", max_length=64
        ) or self._runtime_metadata_text(metadata, "client_version", max_length=64)
        client_mode = self._runtime_metadata_text(
            metadata, "collector_mode", max_length=32
        ) or str(payload.get("source", ""))[:32]
        source_fingerprint = (
            self.crypto.lookup_digest(source_address, "device-source:v1")
            if source_address
            else None
        )
        runtime_is_recent = bool(
            device.runtime_updated_at
            and now - as_utc(device.runtime_updated_at)
            <= timedelta(seconds=settings.device_conflict_window_seconds)
        )
        instance_changed = bool(
            instance_id
            and device.client_instance_id
            and instance_id != device.client_instance_id
        )
        source_changed = bool(
            source_fingerprint
            and device.last_source_fingerprint
            and source_fingerprint != device.last_source_fingerprint
        )
        if runtime_is_recent and (instance_changed or source_changed):
            device.credential_reuse_warning = True
        if instance_id is not None:
            device.client_instance_id = instance_id
        if client_version is not None:
            device.client_version = client_version
        if client_mode:
            device.client_mode = client_mode
        if source_fingerprint is not None:
            device.last_source_fingerprint = source_fingerprint
        device.runtime_updated_at = now
        device.last_seen_at = now

    def _mark_duplicate_collection_warning(
        self,
        session: Session,
        *,
        current_device: DeviceModel,
        payload: dict[str, Any],
        sender: str,
        body: str,
    ) -> None:
        received_at = as_utc(payload["received_at"])
        lower_bound = received_at - timedelta(seconds=5)
        upper_bound = received_at + timedelta(seconds=5)
        # 廉价预过滤：sender+body 摘要等值匹配（SHA-256 命中即内容一致），
        # 避免逐条解密候选消息。历史消息（迁移前）无摘要，不参与匹配。
        sender_body_digest = hashlib.sha256(
            f"{sender}\n{body}".encode()
        ).hexdigest()
        statement = select(MessageModel.device_id).where(
            MessageModel.device_id != current_device.id,
            MessageModel.source != str(payload["source"]),
            MessageModel.redacted.is_(False),
            MessageModel.received_at >= lower_bound,
            MessageModel.received_at <= upper_bound,
            MessageModel.sender_body_digest == sender_body_digest,
        )
        sim_slot = payload.get("sim_slot")
        if sim_slot is None:
            statement = statement.where(MessageModel.sim_slot.is_(None))
        else:
            statement = statement.where(MessageModel.sim_slot == sim_slot)
        other_device_id = session.scalar(statement.limit(1))
        if other_device_id is not None:
            current_device.duplicate_collection_warning = True
            other_device = session.get(DeviceModel, other_device_id)
            if other_device is not None:
                other_device.duplicate_collection_warning = True

    def _message_from_model(self, model: MessageModel) -> Message:
        sender = ""
        body = ""
        metadata = self._message_metadata_from_model(model)
        receiving_phone: str | None = None
        if not model.redacted:
            if model.sender_nonce and model.sender_ciphertext:
                sender = self.crypto.decrypt_text(
                    EncryptedValue(
                        model.sender_nonce,
                        model.sender_ciphertext,
                        model.sender_key_version or 1,
                    ),
                    f"message:{model.id}:sender:v1",
                )
            if model.body_nonce and model.body_ciphertext:
                body = self.crypto.decrypt_text(
                    EncryptedValue(
                        model.body_nonce,
                        model.body_ciphertext,
                        model.body_key_version or 1,
                    ),
                    f"message:{model.id}:body:v1",
                )
            if model.receiving_phone_nonce and model.receiving_phone_ciphertext:
                receiving_phone = self.crypto.decrypt_text(
                    EncryptedValue(
                        model.receiving_phone_nonce,
                        model.receiving_phone_ciphertext,
                        model.receiving_phone_key_version or 1,
                    ),
                    f"message:{model.id}:receiving-phone:v1",
                )
        return Message(
            id=UUID(model.id),
            device_id=UUID(model.device_id),
            client_message_id=model.client_message_id,
            received_at=as_utc(model.received_at),
            sender=sender,
            body=body,
            source=model.source,
            sim_slot=model.sim_slot,
            metadata=metadata,
            receiving_phone=receiving_phone,
            receiving_identity_status=model.receiving_identity_status,
            receiving_phone_lookup_hash=model.receiving_phone_lookup_hash,
            read_at=as_utc(model.read_at) if model.read_at else None,
            created_at=as_utc(model.created_at),
            redacted=model.redacted,
        )

    def _message_metadata_from_model(self, model: MessageModel) -> dict[str, Any]:
        if model.redacted or not model.metadata_nonce or not model.metadata_ciphertext:
            return {}
        raw_metadata = self.crypto.decrypt_text(
            EncryptedValue(
                model.metadata_nonce,
                model.metadata_ciphertext,
                model.metadata_key_version or 1,
            ),
            f"message:{model.id}:metadata:v1",
        )
        parsed = json.loads(raw_metadata)
        return parsed if isinstance(parsed, dict) else {}

    def _profile_phone(self, model: DeviceSimProfileModel) -> str | None:
        if not model.phone_number_nonce or not model.phone_number_ciphertext:
            return None
        stored = self.crypto.decrypt_text(
            EncryptedValue(
                model.phone_number_nonce,
                model.phone_number_ciphertext,
                model.phone_number_key_version or 1,
            ),
            f"device-sim-profile:{model.device_id}:{model.slot_index}:phone:v1",
        )
        # 绑定号码可能是 E.164 统一之前写入的裸 11 位。消息路由、Claim
        # 和 WebSocket 都按精确字符串比较，所以读取时统一规范化，
        # 否则存量行会永远匹配不上脚本订阅的号码。
        try:
            return normalize_phone_number(stored)
        except ValueError:
            return stored

    def _upsert_observed_sim_profile(
        self,
        session: Session,
        *,
        device_id: str,
        sim_slot: object,
        metadata: object,
    ) -> DeviceSimProfileModel | None:
        if not isinstance(metadata, dict):
            metadata = {}
        subscription_value = metadata.get("subscription_id")
        subscription_id = (
            subscription_value
            if isinstance(subscription_value, int)
            and not isinstance(subscription_value, bool)
            and subscription_value >= 0
            else None
        )
        slot_index = (
            sim_slot
            if isinstance(sim_slot, int) and not isinstance(sim_slot, bool) and sim_slot >= 0
            else None
        )
        model: DeviceSimProfileModel | None = None
        if slot_index is not None:
            model = session.get(DeviceSimProfileModel, (device_id, slot_index))
        elif subscription_id is not None:
            model = session.scalar(
                select(DeviceSimProfileModel).where(
                    DeviceSimProfileModel.device_id == device_id,
                    DeviceSimProfileModel.subscription_id == subscription_id,
                )
            )
        if model is None and slot_index is None:
            return None
        if model is None:
            model = DeviceSimProfileModel(
                device_id=device_id,
                slot_index=slot_index,
                subscription_id=subscription_id,
                source="auto",
            )
            session.add(model)
        elif subscription_id is not None and (
            model.source != "manual" or model.subscription_id is None
        ):
            model.subscription_id = subscription_id

        carrier_name = metadata.get("carrier_name")
        if isinstance(carrier_name, str) and carrier_name.strip():
            model.carrier_name = carrier_name.strip()

        raw_phone = metadata.get("phone_number")
        automatic_phone: str | None = None
        if isinstance(raw_phone, str):
            try:
                automatic_phone = normalize_phone_number(raw_phone)
            except ValueError:
                automatic_phone = None
        if automatic_phone is not None and model.source != "manual":
            encrypted = self.crypto.encrypt_text(
                automatic_phone,
                f"device-sim-profile:{device_id}:{model.slot_index}:phone:v1",
            )
            model.phone_number_nonce = encrypted.nonce
            model.phone_number_ciphertext = encrypted.ciphertext
            model.phone_number_key_version = encrypted.key_version
        model.updated_at = utc_now()
        return model

    def _resolve_receiving_identity(
        self,
        session: Session,
        *,
        device_id: str,
        message_id: UUID,
        payload: dict[str, Any],
    ) -> tuple[str | None, str]:
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        raw_subscription_id = metadata.get("subscription_id")
        subscription_id = (
            raw_subscription_id
            if isinstance(raw_subscription_id, int) and not isinstance(raw_subscription_id, bool)
            else None
        )
        raw_slot = payload.get("sim_slot")
        sim_slot = raw_slot if isinstance(raw_slot, int) and not isinstance(raw_slot, bool) else None
        profiles = session.scalars(
            select(DeviceSimProfileModel)
            .where(DeviceSimProfileModel.device_id == device_id)
            .with_for_update()
        ).all()

        if subscription_id is not None:
            subscription_matches = [
                profile for profile in profiles if profile.subscription_id == subscription_id
            ]
            phones = {
                phone
                for profile in subscription_matches
                if (phone := self._profile_phone(profile)) is not None
            }
            if len(phones) > 1:
                return None, "conflict"
            if len(phones) == 1:
                return phones.pop(), "resolved"

        slot_profile = next(
            (profile for profile in profiles if profile.slot_index == sim_slot),
            None,
        )
        if slot_profile is not None:
            slot_phone = self._profile_phone(slot_profile)
            if slot_phone is not None:
                return slot_phone, "resolved"

        automatic_phone: str | None = None
        raw_phone = metadata.get("phone_number")
        if isinstance(raw_phone, str):
            try:
                automatic_phone = normalize_phone_number(raw_phone)
            except ValueError:
                automatic_phone = None

        saved_phones = {
            phone
            for profile in profiles
            if (phone := self._profile_phone(profile)) is not None
        }
        # If Shortcut Input carries the receiving line, prefer it when it is
        # one of the administrator-bound lines. This disambiguates dual-SIM
        # iPhones without making Subscription metadata mandatory.
        if automatic_phone is not None and automatic_phone in saved_phones:
            return automatic_phone, "resolved"

        # A single-device deployment often has no usable SIM slot or
        # subscription hint. When the administrator saved exactly one phone
        # for the device, use it as the device-level receiving identity.
        if len(profiles) <= 1 and len(saved_phones) == 1:
            return saved_phones.pop(), "resolved"
        if len(saved_phones) > 1:
            return None, "conflict"
        if automatic_phone is None:
            return None, "unresolved"

        if slot_profile is not None and slot_profile.source == "manual":
            return None, "unresolved"
        return automatic_phone, "resolved"

    def _reconcile_device_receiving_identities(
        self,
        session: Session,
        *,
        device_id: UUID,
    ) -> None:
        """Re-evaluate unresolved message routing after SIM metadata changes.

        SIM phone numbers are intentionally stored separately from messages. A
        message can therefore arrive before an administrator binds its slot.
        Reconciliation updates only the encrypted routing fields; message body,
        sender, OTP state, and read state remain unchanged.
        """
        models = session.scalars(
            select(MessageModel).where(
                MessageModel.device_id == str(device_id),
                MessageModel.redacted.is_(False),
            )
        ).all()
        for model in models:
            metadata: dict[str, Any] = {}
            if model.metadata_nonce and model.metadata_ciphertext:
                try:
                    raw_metadata = self.crypto.decrypt_text(
                        EncryptedValue(
                            model.metadata_nonce,
                            model.metadata_ciphertext,
                            model.metadata_key_version or 1,
                        ),
                        f"message:{model.id}:metadata:v1",
                    )
                    decoded = json.loads(raw_metadata)
                    if isinstance(decoded, dict):
                        metadata = decoded
                except (InvalidTag, TypeError, ValueError, json.JSONDecodeError):
                    continue

            payload: dict[str, Any] = {"metadata": metadata}
            if model.sim_slot is not None:
                payload["sim_slot"] = model.sim_slot
            phone, status = self._resolve_receiving_identity(
                session,
                device_id=str(device_id),
                message_id=UUID(model.id),
                payload=payload,
            )
            phone_value: EncryptedValue | None = None
            lookup_hash: str | None = None
            if phone is not None:
                phone_value = self.crypto.encrypt_text(
                    phone,
                    f"message:{model.id}:receiving-phone:v1",
                )
                lookup_hash = self.crypto.lookup_digest(phone, "receiving-phone:v1")
            model.receiving_phone_lookup_hash = lookup_hash
            model.receiving_phone_nonce = phone_value.nonce if phone_value else None
            model.receiving_phone_ciphertext = phone_value.ciphertext if phone_value else None
            model.receiving_phone_key_version = phone_value.key_version if phone_value else None
            model.receiving_identity_status = status

    def _otp_from_model(self, model: OtpModel) -> Otp:
        code = ""
        if model.code_nonce and model.code_ciphertext:
            code = self.crypto.decrypt_text(
                EncryptedValue(
                    model.code_nonce,
                    model.code_ciphertext,
                    model.code_key_version or 1,
                ),
                f"otp:{model.id}:code:v1",
            )
        return Otp(
            id=UUID(model.id),
            message_id=UUID(model.message_id),
            code=code,
            expires_at=as_utc(model.expires_at),
            status=model.status,
            claimed_at=as_utc(model.claimed_at) if model.claimed_at else None,
        )

    def _sim_profile_from_model(self, model: DeviceSimProfileModel) -> DeviceSimProfile:
        return DeviceSimProfile(
            device_id=UUID(model.device_id),
            slot_index=model.slot_index,
            subscription_id=model.subscription_id,
            phone_number=self._profile_phone(model),
            carrier_name=model.carrier_name,
            source=model.source,
            updated_at=as_utc(model.updated_at),
        )

    def create_device(self, name: str, client_type: str, auth_kind: str) -> tuple[Device, str]:
        expected = SUPPORTED_DEVICE_CONTRACTS.get(client_type)
        if expected is None or expected[0] != auth_kind:
            raise ValueError("client_type and auth_kind are not a supported pair")

        device_id = uuid4()
        credential = new_bearer("srh_api_") if auth_kind == "script_api" else (
            new_bearer() if auth_kind == "bearer" else new_hmac_secret()
        )
        model = DeviceModel(
            id=str(device_id),
            name=name.strip() or "Unnamed device",
            client_type=client_type,
            auth_kind=auth_kind,
            active=True,
        )
        if auth_kind in {"bearer", "script_api"}:
            model.credential_hash = token_hash(credential)
        elif auth_kind == "hmac":
            encrypted = self.crypto.encrypt_text(credential, f"device:{device_id}:secret:v1")
            model.hmac_secret_nonce = encrypted.nonce
            model.hmac_secret_ciphertext = encrypted.ciphertext
            model.hmac_secret_key_version = encrypted.key_version
        with self.session_factory.begin() as session:
            session.add(model)
            if auth_kind == "script_api":
                client_id = uuid4()
                encrypted = self.crypto.encrypt_text(credential, f"api-client:{client_id}:token:v1")
                session.add(
                    ApiClientModel(
                        id=str(client_id),
                        device_id=str(device_id),
                        token_hash=token_hash(credential),
                        token_nonce=encrypted.nonce,
                        token_ciphertext=encrypted.ciphertext,
                        token_key_version=encrypted.key_version,
                        active=True,
                    )
                )
                self._audit(session, "api_client.created", client_id)
            self._audit(session, "device.created", device_id)
        return self._device_from_model(model), credential

    def create_enrollment_token(
        self,
        device_id: UUID,
        *,
        ttl_seconds: int = 300,
    ) -> DeviceEnrollmentTicket:
        if ttl_seconds < 60 or ttl_seconds > 900:
            raise ValueError("enrollment token TTL must be between 60 and 900 seconds")
        with self.lock:
            with self.session_factory.begin() as session:
                model = self._device_for_update(session, device_id)
                if model is None:
                    raise KeyError(device_id)
                if not model.active:
                    raise ValueError("device_not_active")
                if model.client_type not in {"android_app", "root_module"} or model.auth_kind != "hmac":
                    raise ValueError("device_enrollment_unsupported")
                session.execute(
                    delete(DeviceEnrollmentTokenModel).where(
                        DeviceEnrollmentTokenModel.device_id == str(device_id)
                    )
                )
                raw_token = secrets.token_urlsafe(32)
                expires_at = utc_now() + timedelta(seconds=ttl_seconds)
                session.add(
                    DeviceEnrollmentTokenModel(
                        id=str(uuid4()),
                        device_id=str(device_id),
                        token_hash=token_hash(raw_token),
                        expires_at=expires_at,
                    )
                )
                self._audit(
                    session,
                    "device.enrollment_token_created",
                    device_id,
                    {"ttl_seconds": ttl_seconds},
                )
                return DeviceEnrollmentTicket(
                    device_id=device_id,
                    token=raw_token,
                    expires_at=expires_at,
                )

    def consume_enrollment_token(
        self,
        device_id: UUID,
        raw_token: str,
    ) -> DeviceEnrollmentCredential:
        normalized_token = raw_token.strip()
        if len(normalized_token) < 32 or len(normalized_token) > 256:
            raise ValueError("invalid_enrollment_token")
        with self.lock:
            with self.session_factory.begin() as session:
                token_digest = token_hash(normalized_token)
                token_model = session.scalar(
                    select(DeviceEnrollmentTokenModel)
                    .where(DeviceEnrollmentTokenModel.token_hash == token_digest)
                )
                now = utc_now()
                if (
                    token_model is None
                    or token_model.device_id != str(device_id)
                    or token_model.consumed_at is not None
                ):
                    raise ValueError("invalid_enrollment_token")
                if as_utc(token_model.expires_at) <= now:
                    raise ValueError("enrollment_token_expired")
                claimed = cast(
                    CursorResult[Any],
                    session.execute(
                    update(DeviceEnrollmentTokenModel)
                    .where(
                        DeviceEnrollmentTokenModel.token_hash == token_digest,
                        DeviceEnrollmentTokenModel.device_id == str(device_id),
                        DeviceEnrollmentTokenModel.consumed_at.is_(None),
                        DeviceEnrollmentTokenModel.expires_at > now,
                    )
                    .values(consumed_at=now)
                    .execution_options(synchronize_session=False)
                    ),
                )
                if claimed.rowcount != 1:
                    raise ValueError("invalid_enrollment_token")
                model = session.scalar(
                    select(DeviceModel)
                    .where(DeviceModel.id == str(device_id))
                    .with_for_update()
                )
                if (
                    model is None
                    or not model.active
                    or model.client_type not in {"android_app", "root_module"}
                    or model.auth_kind != "hmac"
                    or not model.hmac_secret_nonce
                    or not model.hmac_secret_ciphertext
                ):
                    raise ValueError("device_enrollment_unavailable")
                secret = self.crypto.decrypt_text(
                    EncryptedValue(
                        model.hmac_secret_nonce,
                        model.hmac_secret_ciphertext,
                        model.hmac_secret_key_version or 1,
                    ),
                    f"device:{model.id}:secret:v1",
                )
                self._audit(session, "device.enrollment_token_consumed", device_id)
                return DeviceEnrollmentCredential(
                    device_id=device_id,
                    device_name=model.name,
                    client_type=model.client_type,
                    auth_kind=model.auth_kind,
                    hmac_secret=secret,
                )

    def _invalidate_enrollment_tokens(self, session: Session, device_id: UUID) -> None:
        session.execute(
            delete(DeviceEnrollmentTokenModel).where(
                DeviceEnrollmentTokenModel.device_id == str(device_id)
            )
        )

    def list_devices(self) -> list[Device]:
        with self.session_factory() as session:
            models = session.scalars(select(DeviceModel).order_by(DeviceModel.created_at)).all()
            return [self._device_from_model(model) for model in models]

    def get_device(self, device_id: UUID) -> Device | None:
        with self.session_factory() as session:
            model = session.get(DeviceModel, str(device_id))
            return self._device_from_model(model) if model else None

    def find_bearer_device(self, raw_token: str) -> Device | None:
        hashed = token_hash(raw_token)
        with self.session_factory() as session:
            model = session.scalar(
                select(DeviceModel).where(
                    DeviceModel.active.is_(True),
                    DeviceModel.auth_kind == "bearer",
                    DeviceModel.credential_hash == hashed,
                )
            )
            return self._device_from_model(model) if model else None

    def get_hmac_device(self, device_id: UUID) -> Device | None:
        with self.session_factory() as session:
            model = session.scalar(
                select(DeviceModel).where(
                    DeviceModel.id == str(device_id),
                    DeviceModel.active.is_(True),
                    DeviceModel.auth_kind == "hmac",
                )
            )
            return self._device_from_model(model, include_secret=True) if model else None

    def rotate_device(self, device_id: UUID) -> tuple[Device, str]:
        with self.lock:
            with self.session_factory.begin() as session:
                model = self._device_for_update(session, device_id)
                if not model:
                    raise KeyError(device_id)
                if not model.active:
                    raise ValueError("device_not_active")
                self._invalidate_enrollment_tokens(session, device_id)
                credential = (
                    new_bearer("srh_api_")
                    if model.auth_kind == "script_api"
                    else new_bearer()
                    if model.auth_kind == "bearer"
                    else new_hmac_secret()
                )
                if model.auth_kind in {"bearer", "script_api"}:
                    model.credential_hash = token_hash(credential)
                elif model.auth_kind == "hmac":
                    encrypted = self.crypto.encrypt_text(
                        credential, f"device:{device_id}:secret:v1"
                    )
                    model.hmac_secret_nonce = encrypted.nonce
                    model.hmac_secret_ciphertext = encrypted.ciphertext
                    model.hmac_secret_key_version = encrypted.key_version
                if model.auth_kind == "script_api":
                    api_client = session.scalar(
                        select(ApiClientModel).where(ApiClientModel.device_id == str(device_id)).with_for_update()
                    )
                    if api_client is None:
                        raise RuntimeError("air724_script_token_missing")
                    encrypted = self.crypto.encrypt_text(credential, f"api-client:{api_client.id}:token:v1")
                    api_client.token_hash = token_hash(credential)
                    api_client.token_nonce = encrypted.nonce
                    api_client.token_ciphertext = encrypted.ciphertext
                    api_client.token_key_version = encrypted.key_version
                    api_client.active = True
                model.updated_at = utc_now()
                self._audit(session, "device.rotated", device_id)
            return self._device_from_model(model), credential

    def revoke_device(self, device_id: UUID) -> None:
        with self.lock:
            with self.session_factory.begin() as session:
                model = self._device_for_update(session, device_id)
                if not model:
                    raise KeyError(device_id)
                self._invalidate_enrollment_tokens(session, device_id)
                model.active = False
                model.updated_at = utc_now()
                if model.auth_kind == "script_api":
                    api_client = session.scalar(
                        select(ApiClientModel).where(ApiClientModel.device_id == str(device_id)).with_for_update()
                    )
                    if api_client is not None:
                        api_client.active = False
                self._audit(session, "device.revoked", device_id)

    def restore_device(self, device_id: UUID) -> tuple[Device, str]:
        with self.lock:
            with self.session_factory.begin() as session:
                model = self._device_for_update(session, device_id)
                if not model:
                    raise KeyError(device_id)
                if model.active:
                    raise ValueError("device_not_revoked")
                self._invalidate_enrollment_tokens(session, device_id)

                credential = (
                    new_bearer("srh_api_")
                    if model.auth_kind == "script_api"
                    else new_bearer()
                    if model.auth_kind == "bearer"
                    else new_hmac_secret()
                )
                if model.auth_kind in {"bearer", "script_api"}:
                    model.credential_hash = token_hash(credential)
                    model.hmac_secret_nonce = None
                    model.hmac_secret_ciphertext = None
                    model.hmac_secret_key_version = None
                elif model.auth_kind == "hmac":
                    encrypted = self.crypto.encrypt_text(
                        credential, f"device:{device_id}:secret:v1"
                    )
                    model.credential_hash = None
                    model.hmac_secret_nonce = encrypted.nonce
                    model.hmac_secret_ciphertext = encrypted.ciphertext
                    model.hmac_secret_key_version = encrypted.key_version
                if model.auth_kind == "script_api":
                    api_client = session.scalar(
                        select(ApiClientModel).where(ApiClientModel.device_id == str(device_id)).with_for_update()
                    )
                    if api_client is None:
                        raise RuntimeError("air724_script_token_missing")
                    encrypted = self.crypto.encrypt_text(credential, f"api-client:{api_client.id}:token:v1")
                    api_client.token_hash = token_hash(credential)
                    api_client.token_nonce = encrypted.nonce
                    api_client.token_ciphertext = encrypted.ciphertext
                    api_client.token_key_version = encrypted.key_version
                    api_client.active = True
                model.active = True
                model.updated_at = utc_now()
                self._audit(session, "device.restored", device_id)
            return self._device_from_model(model), credential

    def purge_device(self, device_id: UUID) -> None:
        with self.lock:
            with self.session_factory.begin() as session:
                model = self._device_for_update(session, device_id)
                if not model:
                    raise KeyError(device_id)
                if model.active:
                    raise ValueError("device_not_revoked")
                self._invalidate_enrollment_tokens(session, device_id)

                message_ids = select(MessageModel.id).where(
                    MessageModel.device_id == str(device_id)
                )
                session.execute(delete(OtpModel).where(OtpModel.message_id.in_(message_ids)))
                session.execute(
                    delete(MessageModel).where(MessageModel.device_id == str(device_id))
                )
                session.execute(
                    delete(DeviceNonceModel).where(DeviceNonceModel.device_id == str(device_id))
                )
                session.execute(
                    delete(DeviceSimProfileModel).where(
                        DeviceSimProfileModel.device_id == str(device_id)
                    )
                )
                session.execute(delete(ApiClientModel).where(ApiClientModel.device_id == str(device_id)))
                session.delete(model)
                self._audit(session, "device.purged", device_id)

    def list_sim_profiles(self, device_id: UUID) -> list[DeviceSimProfile]:
        with self.lock:
            with self.session_factory.begin() as session:
                if self._device_for_update(session, device_id) is None:
                    raise KeyError(device_id)
                messages = session.scalars(
                    select(MessageModel)
                    .where(
                        MessageModel.device_id == str(device_id),
                        MessageModel.sim_slot.is_not(None),
                    )
                    .order_by(MessageModel.created_at)
                ).all()
                for message in messages:
                    self._upsert_observed_sim_profile(
                        session,
                        device_id=str(device_id),
                        sim_slot=message.sim_slot,
                        metadata=self._message_metadata_from_model(message),
                    )
                models = session.scalars(
                    select(DeviceSimProfileModel)
                    .where(DeviceSimProfileModel.device_id == str(device_id))
                    .order_by(DeviceSimProfileModel.slot_index)
                ).all()
                return [self._sim_profile_from_model(model) for model in models]

    def get_sim_profile(self, device_id: UUID, slot_index: int) -> DeviceSimProfile | None:
        with self.session_factory() as session:
            if session.get(DeviceModel, str(device_id)) is None:
                raise KeyError(device_id)
            model = session.scalar(
                select(DeviceSimProfileModel).where(
                    DeviceSimProfileModel.device_id == str(device_id),
                    DeviceSimProfileModel.slot_index == slot_index,
                )
            )
            return self._sim_profile_from_model(model) if model else None

    def upsert_sim_profile(
        self,
        device_id: UUID,
        slot_index: int,
        updates: dict[str, Any],
    ) -> DeviceSimProfile:
        normalized_updates = dict(updates)
        if isinstance(normalized_updates.get("phone_number"), str):
            normalized_updates["phone_number"] = normalize_phone_number(
                normalized_updates["phone_number"]
            )
        with self.lock:
            with self.session_factory.begin() as session:
                if self._device_for_update(session, device_id) is None:
                    raise KeyError(device_id)
                model = session.scalar(
                    select(DeviceSimProfileModel).where(
                        DeviceSimProfileModel.device_id == str(device_id),
                        DeviceSimProfileModel.slot_index == slot_index,
                    )
                )
                if model is None:
                    model = DeviceSimProfileModel(
                        device_id=str(device_id),
                        slot_index=slot_index,
                        subscription_id=normalized_updates.get("subscription_id"),
                        carrier_name=normalized_updates.get("carrier_name"),
                        source=normalized_updates.get("source", "manual"),
                    )
                    session.add(model)
                else:
                    for field_name in (
                        "subscription_id",
                        "carrier_name",
                        "source",
                    ):
                        if field_name in normalized_updates:
                            setattr(model, field_name, normalized_updates[field_name])
                    model.updated_at = utc_now()
                if "phone_number" in normalized_updates:
                    phone_number = normalized_updates["phone_number"]
                    if phone_number is None:
                        model.phone_number_nonce = None
                        model.phone_number_ciphertext = None
                        model.phone_number_key_version = None
                    else:
                        encrypted = self.crypto.encrypt_text(
                            phone_number,
                            f"device-sim-profile:{device_id}:{slot_index}:phone:v1",
                        )
                        model.phone_number_nonce = encrypted.nonce
                        model.phone_number_ciphertext = encrypted.ciphertext
                        model.phone_number_key_version = encrypted.key_version
                session.flush()
                self._reconcile_device_receiving_identities(session, device_id=device_id)
            return self._sim_profile_from_model(model)

    def delete_sim_profile(self, device_id: UUID, slot_index: int) -> None:
        with self.lock:
            with self.session_factory.begin() as session:
                if self._device_for_update(session, device_id) is None:
                    raise KeyError(device_id)
                model = session.scalar(
                    select(DeviceSimProfileModel).where(
                        DeviceSimProfileModel.device_id == str(device_id),
                        DeviceSimProfileModel.slot_index == slot_index,
                    )
                )
                if model is None:
                    raise LookupError((device_id, slot_index))
                session.delete(model)
                session.flush()
                self._reconcile_device_receiving_identities(session, device_id=device_id)

    def accept_nonce(self, device_id: UUID, nonce: str) -> bool:
        with self.lock:
            try:
                with self.session_factory.begin() as session:
                    session.add(DeviceNonceModel(device_id=str(device_id), nonce=nonce))
                return True
            except IntegrityError:
                return False

    def ingest(
        self,
        *,
        raw_body: bytes,
        authorization: str | None,
        device_id: str | None,
        timestamp: str | None,
        nonce: str | None,
        signature: str | None,
        idempotency_key: str | None,
        payload: dict[str, Any],
        request_hash: str,
        source_address: str | None = None,
        allow_nonce_replay_for_idempotent: bool = False,
    ) -> tuple[Message, bool]:
        with self.lock:
            with self.session_factory.begin() as session:
                self._begin_write_transaction(session)
                device_model = self._authenticate_ingest_device(
                    session,
                    raw_body=raw_body,
                    authorization=authorization,
                    device_id=device_id,
                    timestamp=timestamp,
                    nonce=nonce,
                    signature=signature,
                )
                expected = DEVICE_CONTRACTS.get(device_model.client_type)
                if expected is None or payload.get("source") != expected[1]:
                    raise ValueError("source does not match the enrolled device type")
                if not idempotency_key or payload.get("client_message_id") != idempotency_key:
                    raise ValueError("idempotency key must match client_message_id")
                runtime_now = utc_now()
                self._update_device_runtime(
                    device_model,
                    payload=payload,
                    source_address=source_address,
                    now=runtime_now,
                )
                prior = None
                if allow_nonce_replay_for_idempotent:
                    prior = session.scalar(
                        select(MessageModel).where(
                            MessageModel.device_id == device_model.id,
                            MessageModel.client_message_id == payload["client_message_id"],
                        )
                    )
                    if prior:
                        if prior.request_hash != request_hash:
                            raise IdempotencyConflict()
                        return self._message_from_model(prior), True
                if device_model.auth_kind == "hmac":
                    if not nonce:
                        raise InvalidDeviceCredential()
                    prior_nonce = session.scalar(
                        select(DeviceNonceModel.id).where(
                            DeviceNonceModel.device_id == device_model.id,
                            DeviceNonceModel.nonce == nonce,
                        )
                    )
                    if prior_nonce is not None:
                        raise InvalidDeviceCredential()
                    session.add(DeviceNonceModel(device_id=device_model.id, nonce=nonce))

                if prior is None:
                    prior = session.scalar(
                        select(MessageModel).where(
                            MessageModel.device_id == device_model.id,
                            MessageModel.client_message_id == payload["client_message_id"],
                        )
                    )
                if prior:
                    if prior.request_hash != request_hash:
                        raise IdempotencyConflict()
                    return self._message_from_model(prior), True

                message_id = uuid4()
                sender = str(payload["sender"])
                body = str(payload["body"])
                sender_body_digest = hashlib.sha256(
                    f"{sender}\n{body}".encode()
                ).hexdigest()
                metadata = payload.get("metadata", {})
                self._mark_duplicate_collection_warning(
                    session,
                    current_device=device_model,
                    payload=payload,
                    sender=sender,
                    body=body,
                )
                sender_value = self.crypto.encrypt_text(
                    sender, f"message:{message_id}:sender:v1"
                )
                body_value = self.crypto.encrypt_text(body, f"message:{message_id}:body:v1")
                metadata_value = self.crypto.encrypt_text(
                    json.dumps(metadata, separators=(",", ":"), sort_keys=True),
                    f"message:{message_id}:metadata:v1",
                )
                self._upsert_observed_sim_profile(
                    session,
                    device_id=device_model.id,
                    sim_slot=payload.get("sim_slot"),
                    metadata=metadata,
                )
                receiving_phone, receiving_identity_status = self._resolve_receiving_identity(
                    session,
                    device_id=device_model.id,
                    message_id=message_id,
                    payload=payload,
                )
                receiving_phone_value: EncryptedValue | None = None
                receiving_phone_lookup_hash: str | None = None
                if receiving_phone is not None:
                    receiving_phone_value = self.crypto.encrypt_text(
                        receiving_phone,
                        f"message:{message_id}:receiving-phone:v1",
                    )
                    receiving_phone_lookup_hash = self.crypto.lookup_digest(
                        receiving_phone,
                        "receiving-phone:v1",
                    )
                message_model = MessageModel(
                    id=str(message_id),
                    device_id=device_model.id,
                    client_message_id=str(payload["client_message_id"]),
                    request_hash=request_hash,
                    received_at=as_utc(payload["received_at"]),
                    sender_summary=mask_identifier(sender),
                    sender_nonce=sender_value.nonce,
                    sender_ciphertext=sender_value.ciphertext,
                    sender_key_version=sender_value.key_version,
                    body_nonce=body_value.nonce,
                    body_ciphertext=body_value.ciphertext,
                    body_key_version=body_value.key_version,
                    metadata_nonce=metadata_value.nonce,
                    metadata_ciphertext=metadata_value.ciphertext,
                    metadata_key_version=metadata_value.key_version,
                    source=str(payload["source"]),
                    sim_slot=payload.get("sim_slot"),
                    receiving_phone_lookup_hash=receiving_phone_lookup_hash,
                    receiving_phone_nonce=(
                        receiving_phone_value.nonce if receiving_phone_value else None
                    ),
                    receiving_phone_ciphertext=(
                        receiving_phone_value.ciphertext if receiving_phone_value else None
                    ),
                    receiving_phone_key_version=(
                        receiving_phone_value.key_version if receiving_phone_value else None
                    ),
                    receiving_identity_status=receiving_identity_status,
                    sender_body_digest=sender_body_digest,
                )
                session.add(message_model)
                session.flush()
                code = extract_code(body)
                if code is not None:
                    self._add_otp_model(session, message_id, code, code_expiry())
                self._audit(session, "ingest.accepted", message_id)
            return self._message_from_model(message_model), False

    def _authenticate_ingest_device(
        self,
        session: Session,
        *,
        raw_body: bytes,
        authorization: str | None,
        device_id: str | None,
        timestamp: str | None,
        nonce: str | None,
        signature: str | None,
    ) -> DeviceModel:
        bearer_token: str | None = None
        if authorization and authorization.lower().startswith("bearer "):
            bearer_token = authorization.split(" ", 1)[1]
        if bearer_token:
            model = session.scalar(
                select(DeviceModel)
                .where(
                    DeviceModel.auth_kind == "bearer",
                    DeviceModel.credential_hash == token_hash(bearer_token),
                )
                .with_for_update()
            )
            if model is not None and model.active:
                return model

        if not all((device_id, timestamp, nonce, signature)):
            raise InvalidDeviceCredential()
        try:
            parsed_device_id = UUID(device_id or "")
            parsed_timestamp = int(timestamp or "")
        except (TypeError, ValueError):
            raise InvalidDeviceCredential() from None
        if abs(time.time() - parsed_timestamp) > settings.hmac_skew_seconds:
            raise InvalidDeviceCredential()
        model = session.scalar(
            select(DeviceModel)
            .where(DeviceModel.id == str(parsed_device_id), DeviceModel.auth_kind == "hmac")
            .with_for_update()
        )
        if not model or not model.active or not model.hmac_secret_nonce or not model.hmac_secret_ciphertext:
            raise InvalidDeviceCredential()
        secret = self.crypto.decrypt_text(
            EncryptedValue(
                model.hmac_secret_nonce,
                model.hmac_secret_ciphertext,
                model.hmac_secret_key_version or 1,
            ),
            f"device:{model.id}:secret:v1",
        )
        if not verify_hmac(
            secret,
            timestamp or "",
            nonce or "",
            raw_body,
            signature or "",
        ):
            raise InvalidDeviceCredential()
        return model

    def add_otp(self, message_id: UUID, code: str, expires_at: datetime) -> Otp:
        with self.session_factory.begin() as session:
            model = self._add_otp_model(session, message_id, code, expires_at)
        return self._otp_from_model(model)

    def _add_otp_model(
        self,
        session: Session,
        message_id: UUID,
        code: str,
        expires_at: datetime,
    ) -> OtpModel:
        otp_id = uuid4()
        encrypted = self.crypto.encrypt_text(code, f"otp:{otp_id}:code:v1")
        model = OtpModel(
            id=str(otp_id),
            message_id=str(message_id),
            code_nonce=encrypted.nonce,
            code_ciphertext=encrypted.ciphertext,
            code_key_version=encrypted.key_version,
            expires_at=as_utc(expires_at),
            status="available",
        )
        session.add(model)
        self._audit(session, "access_code.parsed", otp_id)
        return model

    def create_api_client(self) -> str:
        raw = new_bearer("srh_api_")
        client_id = uuid4()
        encrypted = self.crypto.encrypt_text(raw, f"api-client:{client_id}:token:v1")
        with self.session_factory.begin() as session:
            model = ApiClientModel(
                id=str(client_id),
                token_hash=token_hash(raw),
                token_nonce=encrypted.nonce,
                token_ciphertext=encrypted.ciphertext,
                token_key_version=encrypted.key_version,
                active=True,
            )
            session.add(model)
            self._audit(session, "api_client.created", model.id)
        return raw

    def list_api_clients(self) -> list[dict[str, object]]:
        with self.session_factory() as session:
            models = session.scalars(
                select(ApiClientModel)
                .where(ApiClientModel.device_id.is_(None))
                .order_by(ApiClientModel.created_at.desc())
            ).all()
            clients: list[dict[str, object]] = []
            for model in models:
                raw_token: str | None = None
                if model.token_nonce and model.token_ciphertext:
                    try:
                        raw_token = self.crypto.decrypt_text(
                            EncryptedValue(
                                model.token_nonce,
                                model.token_ciphertext,
                                model.token_key_version or 1,
                            ),
                            f"api-client:{model.id}:token:v1",
                        )
                    except (InvalidTag, UnicodeDecodeError):
                        raw_token = None
                clients.append(
                    {
                        "id": model.id,
                        "device_id": model.device_id,
                        "token": raw_token,
                        "active": model.active,
                        "created_at": as_utc(model.created_at).isoformat(),
                    }
                )
            return clients

    def authenticate_api_client(self, raw_token: str) -> bool:
        with self.session_factory.begin() as session:
            model = session.scalar(
                select(ApiClientModel).where(
                    ApiClientModel.token_hash == token_hash(raw_token),
                    ApiClientModel.active.is_(True),
                )
            )
            if model is None:
                return False
            if model.device_id:
                device = session.get(DeviceModel, model.device_id)
                if device is None or not device.active:
                    return False
                device.last_seen_at = utc_now()
                device.updated_at = utc_now()
            return True

    def _cleanup_otps(self, session: Session, now: datetime) -> None:
        claimed_cutoff = now - timedelta(seconds=settings.claimed_otp_retention_seconds)
        session.execute(
            delete(OtpModel).where(
                (OtpModel.status == "expired")
                | ((OtpModel.status == "available") & (OtpModel.expires_at <= now))
                | (
                    (OtpModel.status == "claimed")
                    & OtpModel.claimed_at.is_not(None)
                    & (OtpModel.claimed_at <= claimed_cutoff)
                )
            )
        )

    def cleanup_otps(self) -> None:
        now = utc_now()
        with self.lock:
            with self.session_factory.begin() as session:
                self._cleanup_otps(session, now)
                self._cleanup_nonces(session, now)

    def _cleanup_nonces(self, session: Session, now: datetime) -> None:
        """HMAC 时间戳窗口只有 ±300 秒，超过 1 天的 nonce 永远无法再通过校验，安全删除。"""
        cutoff = now - timedelta(hours=24)
        session.execute(delete(DeviceNonceModel).where(DeviceNonceModel.created_at < cutoff))

    def _cleanup_forwarding_logs(self, session: Session, now: datetime) -> None:
        cutoff = now - timedelta(days=7)
        session.execute(delete(ForwardingLogModel).where(ForwardingLogModel.created_at < cutoff))

    def list_otps(self) -> list[Otp]:
        self.cleanup_otps()
        with self.session_factory() as session:
            models = session.scalars(select(OtpModel).order_by(OtpModel.created_at.desc())).all()
            return [self._otp_from_model(model) for model in models]

    def list_messages(
        self,
        *,
        phone_number: str | None = None,
        device_id: UUID | None = None,
        sim_slot: int | None = None,
        sender: str | None = None,
        keyword: str | None = None,
        identity_status: str | None = None,
        otp_status: str | None = None,
        read: bool | None = None,
        created_after: datetime | None = None,
        after_id: UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Message]:
        if (created_after is None) != (after_id is None):
            raise ValueError("created_after and after_id must be provided together")
        if created_after is not None and (
            created_after.tzinfo is None or created_after.utcoffset() is None
        ):
            raise ValueError("created_after must include a timezone")
        self.cleanup_otps()
        with self.session_factory() as session:
            statement = select(MessageModel)
            if created_after is None:
                statement = statement.order_by(
                    MessageModel.created_at.desc(), MessageModel.id.desc()
                )
            else:
                statement = statement.where(
                    or_(
                        MessageModel.created_at > created_after,
                        and_(
                            MessageModel.created_at == created_after,
                            MessageModel.id > str(after_id),
                        ),
                    )
                ).order_by(MessageModel.created_at.asc(), MessageModel.id.asc())
            if phone_number is not None:
                normalized = normalize_phone_number(phone_number)
                statement = statement.where(
                    MessageModel.receiving_phone_lookup_hash
                    == self.crypto.lookup_digest(normalized, "receiving-phone:v1")
                )
            if device_id is not None:
                statement = statement.where(MessageModel.device_id == str(device_id))
            if sim_slot is not None:
                # iPhone Shortcuts do not provide a SIM slot. When a phone
                # profile is bound, match its lookup hash as a fallback.
                slot_profile = None
                if device_id is not None:
                    slot_profile = session.scalar(
                        select(DeviceSimProfileModel).where(
                            DeviceSimProfileModel.device_id == str(device_id),
                            DeviceSimProfileModel.slot_index == sim_slot,
                        )
                    )
                slot_phone_hash = None
                if slot_profile is not None:
                    slot_phone = self._profile_phone(slot_profile)
                    if slot_phone is not None:
                        slot_phone_hash = self.crypto.lookup_digest(
                            normalize_phone_number(slot_phone), "receiving-phone:v1"
                        )
                if slot_phone_hash is None:
                    statement = statement.where(MessageModel.sim_slot == sim_slot)
                else:
                    statement = statement.where(
                        (MessageModel.sim_slot == sim_slot)
                        | (MessageModel.receiving_phone_lookup_hash == slot_phone_hash)
                    )
            if identity_status is not None:
                statement = statement.where(
                    MessageModel.receiving_identity_status == identity_status
                )
            if read is True:
                statement = statement.where(MessageModel.read_at.is_not(None))
            elif read is False:
                statement = statement.where(MessageModel.read_at.is_(None))
            if otp_status is not None:
                statement = statement.where(
                    MessageModel.id.in_(
                        select(OtpModel.message_id).where(OtpModel.status == otp_status)
                    )
                )
            models = session.scalars(statement).all()
            otp_by_message = {
                otp.message_id: otp.status
                for otp in session.scalars(
                    select(OtpModel).where(
                        OtpModel.message_id.in_([model.id for model in models])
                    )
                ).all()
            } if models else {}
            messages: list[Message] = []
            for model in models:
                message = self._message_from_model(model)
                message.otp_status = otp_by_message.get(model.id)
                if sender is not None and message.sender != sender:
                    continue
                if keyword is not None and keyword.casefold() not in (message.body or "").casefold():
                    continue
                messages.append(message)
            return messages[offset : offset + limit]

    def get_message(self, message_id: UUID) -> Message | None:
        self.cleanup_otps()
        with self.session_factory() as session:
            model = session.get(MessageModel, str(message_id))
            if model is None:
                return None
            message = self._message_from_model(model)
            message.otp_status = session.scalar(
                select(OtpModel.status).where(OtpModel.message_id == model.id)
            )
            return message

    def get_otp_delivery(self, message_id: UUID) -> dict[str, Any] | None:
        """Load one committed OTP for live delivery without claiming it."""
        now = utc_now()
        with self.session_factory() as session:
            row = session.execute(
                select(OtpModel, MessageModel)
                .join(MessageModel, MessageModel.id == OtpModel.message_id)
                .where(
                    OtpModel.message_id == str(message_id),
                    OtpModel.status == "available",
                    OtpModel.expires_at > now,
                )
            ).first()
            if row is None:
                return None
            otp_model, message_model = row
            otp = self._otp_from_model(otp_model)
            message = self._message_from_model(message_model)
            return {
                "type": "otp",
                "otp_id": str(otp.id),
                "message_id": str(message.id),
                "device_id": str(message.device_id),
                "phone_number": message.receiving_phone,
                "sender": message.sender,
                "body": message.body,
                "code": otp.code,
                "created_at": message.created_at.isoformat(),
                "received_at": message.received_at.isoformat(),
                "expires_at": otp.expires_at.isoformat(),
            }

    def get_otp_states(self, message_ids: list[UUID]) -> dict[str, dict[str, Any]]:
        """Batch load OTP status (and code when still deliverable), keyed by message id.

        Read-only: nothing is claimed or mutated. ``status`` mirrors the ``otps`` row
        verbatim (the same value list_messages reports), while ``code`` is only filled
        for rows that are still ``available`` and unexpired — exactly the rule
        get_otp_delivery() uses for live delivery. Callers therefore get identical
        values on the REST and WebSocket paths instead of one of them guessing.
        """
        keys = list(dict.fromkeys(str(message_id) for message_id in message_ids))
        if not keys:
            return {}
        now = utc_now()
        with self.session_factory() as session:
            models = session.scalars(
                select(OtpModel).where(OtpModel.message_id.in_(keys))
            ).all()
        states: dict[str, dict[str, Any]] = {}
        for model in models:
            # as_utc: SQLite hands back naive datetimes, and comparing a naive value
            # with the aware ``now`` raises TypeError. Same pattern as device.online.
            deliverable = model.status == "available" and as_utc(model.expires_at) > now
            states[model.message_id] = {
                "status": model.status,
                "code": self._otp_from_model(model).code if deliverable else None,
            }
        return states

    def set_message_read(self, message_id: UUID, *, read: bool) -> Message:
        with self.lock:
            with self.session_factory.begin() as session:
                model = session.get(MessageModel, str(message_id))
                if model is None:
                    raise KeyError(message_id)
                model.read_at = utc_now() if read else None
                self._audit(
                    session,
                    "message.read" if read else "message.unread",
                    message_id,
                )
            message = self._message_from_model(model)
        with self.session_factory() as session:
            message.otp_status = session.scalar(
                select(OtpModel.status).where(OtpModel.message_id == str(message_id))
            )
        return message

    def delete_message(self, message_id: UUID) -> None:
        with self.lock:
            with self.session_factory.begin() as session:
                model = session.get(MessageModel, str(message_id))
                if model is None:
                    raise KeyError(message_id)
                session.execute(delete(OtpModel).where(OtpModel.message_id == str(message_id)))
                session.delete(model)
                self._audit(session, "message.deleted", message_id)

    def delete_messages(self, message_ids: list[UUID]) -> int:
        unique_ids = list(dict.fromkeys(message_ids))
        if not unique_ids:
            return 0
        with self.lock:
            with self.session_factory.begin() as session:
                id_strings = [str(message_id) for message_id in unique_ids]
                existing = session.scalars(
                    select(MessageModel).where(MessageModel.id.in_(id_strings))
                ).all()
                existing_ids = {model.id for model in existing}
                if not existing_ids:
                    return 0
                session.execute(delete(OtpModel).where(OtpModel.message_id.in_(existing_ids)))
                session.execute(delete(MessageModel).where(MessageModel.id.in_(existing_ids)))
                for message_id in existing_ids:
                    self._audit(session, "message.deleted", message_id)
                return len(existing_ids)

    def claim(
        self,
        *,
        device_id: UUID | None = None,
        phone_number: str | None = None,
        sender: str | None = None,
        keyword: str | None = None,
    ) -> Otp:
        now = utc_now()
        with self.lock:
            with self.session_factory.begin() as session:
                self._cleanup_otps(session, now)
                statement = (
                    select(OtpModel, MessageModel)
                    .join(MessageModel, MessageModel.id == OtpModel.message_id)
                    .where(OtpModel.status == "available", OtpModel.expires_at > now)
                    .order_by(MessageModel.created_at.desc(), OtpModel.created_at.desc())
                )
                if device_id:
                    statement = statement.where(MessageModel.device_id == str(device_id))
                if phone_number:
                    normalized = normalize_phone_number(phone_number)
                    statement = statement.where(
                        MessageModel.receiving_phone_lookup_hash
                        == self.crypto.lookup_digest(normalized, "receiving-phone:v1")
                    )
                if self.is_postgres:
                    statement = statement.with_for_update(skip_locked=True, of=OtpModel)
                matching: list[tuple[OtpModel, MessageModel, Message]] = []
                for otp_model, message_model in session.execute(statement).all():
                    message = self._message_from_model(message_model)
                    if sender and sender.casefold() not in message.sender.casefold():
                        continue
                    if keyword and keyword.lower() not in (message.body or "").lower():
                        continue
                    matching.append((otp_model, message_model, message))
                if matching:
                    otp_model, message_model, message = matching[0]
                    otp_model.status = "claimed"
                    otp_model.claimed_at = now
                    self._audit(session, "access_code.claimed", otp_model.id)
                    return self._otp_from_model(otp_model)
        raise LookupError("otp_not_available")

    def run_retention(self, retention_days: int) -> int:
        cutoff = utc_now() - timedelta(days=retention_days)
        nonce_cutoff = utc_now() - timedelta(seconds=settings.hmac_skew_seconds)
        redacted = 0
        with self.lock:
            with self.session_factory.begin() as session:
                session.execute(
                    delete(DeviceNonceModel).where(DeviceNonceModel.created_at <= nonce_cutoff)
                )
                messages = session.scalars(
                    select(MessageModel).where(
                        MessageModel.redacted.is_(False), MessageModel.created_at <= cutoff
                    )
                ).all()
                for message in messages:
                    message.sender_nonce = None
                    message.sender_ciphertext = None
                    message.sender_key_version = None
                    message.body_nonce = None
                    message.body_ciphertext = None
                    message.body_key_version = None
                    message.metadata_nonce = None
                    message.metadata_ciphertext = None
                    message.metadata_key_version = None
                    message.receiving_phone_lookup_hash = None
                    message.receiving_phone_nonce = None
                    message.receiving_phone_ciphertext = None
                    message.receiving_phone_key_version = None
                    message.redacted = True
                    otp = session.scalar(select(OtpModel).where(OtpModel.message_id == message.id))
                    if otp:
                        otp.code_nonce = None
                        otp.code_ciphertext = None
                        otp.code_key_version = None
                        if otp.status == "available":
                            otp.status = "expired"
                    self._audit(session, "message.redacted", message.id)
                    redacted += 1
                self._cleanup_otps(session, utc_now())
                # 发送日志也要按保留期清理：DEFERRED_FEATURES 3.1 承诺"发送日志保留 7 天"，
                # 但 _cleanup_forwarding_logs 此前从未被任何地方调用，forwarding_logs
                # 只增不减。这里补上调用，让承诺与实际一致。
                self._cleanup_forwarding_logs(session, utc_now())
        return redacted

    def age_message(self, message_id: UUID, *, days: int = 0, seconds: int = 0) -> None:
        now = utc_now()
        with self.session_factory.begin() as session:
            message = session.get(MessageModel, str(message_id))
            if not message:
                raise KeyError(message_id)
            if days:
                message.created_at = now - timedelta(days=days)
            otp = session.scalar(select(OtpModel).where(OtpModel.message_id == str(message_id)))
            if otp and seconds:
                otp.expires_at = now - timedelta(seconds=seconds)

    def database_fingerprint(self) -> str:
        database_identity = make_url(self.database_url).set(password=None).render_as_string()
        return hashlib.sha256(database_identity.encode()).hexdigest()[:12]


    # ---------- 转发规则引擎（多渠道推送） ----------

    def _forwarding_rule_from_model(self, model: ForwardingRuleModel) -> dict[str, Any]:
        target = ""
        if model.target_ciphertext is not None and model.target_nonce is not None:
            target = self.crypto.decrypt_text(
                EncryptedValue(
                    model.target_nonce,
                    model.target_ciphertext,
                    model.target_key_version or 1,
                ),
                f"forwarding-rule:{model.id}:target:v1",
            )
        return {
            "id": str(model.id),
            "name": model.name,
            "enabled": bool(model.enabled),
            "match_device_id": model.match_device_id,
            "match_phone_lookup_hash": model.match_phone_lookup_hash,
            "match_sender": model.match_sender,
            "match_keyword": model.match_keyword,
            "channel": model.channel,
            "target": target,
            "template": model.template,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }

    def list_forwarding_rules(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            stmt = select(ForwardingRuleModel).order_by(ForwardingRuleModel.created_at)
            if enabled_only:
                stmt = stmt.where(ForwardingRuleModel.enabled.is_(True))
            return [self._forwarding_rule_from_model(m) for m in session.scalars(stmt).all()]

    def get_forwarding_rule(self, rule_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            model = session.get(ForwardingRuleModel, str(rule_id))
            return self._forwarding_rule_from_model(model) if model is not None else None

    def create_forwarding_rule(self, spec: dict[str, Any]) -> dict[str, Any]:
        rule_id = str(uuid4())
        with self.lock:
            with self.session_factory.begin() as session:
                target_value = self.crypto.encrypt_text(
                    str(spec["target"]), f"forwarding-rule:{rule_id}:target:v1"
                )
                model = ForwardingRuleModel(
                    id=rule_id,
                    name=str(spec["name"])[:120],
                    enabled=bool(spec.get("enabled", True)),
                    match_device_id=spec.get("match_device_id"),
                    match_phone_lookup_hash=(
                        self.crypto.lookup_digest(spec["match_phone"], "receiving-phone:v1")
                        if spec.get("match_phone")
                        else None
                    ),
                    match_sender=spec.get("match_sender"),
                    match_keyword=spec.get("match_keyword"),
                    channel=str(spec["channel"]),
                    target_nonce=target_value.nonce,
                    target_ciphertext=target_value.ciphertext,
                    target_key_version=target_value.key_version,
                    template=spec.get("template"),
                )
                session.add(model)
            return self.get_forwarding_rule(rule_id)

    def update_forwarding_rule(self, rule_id: str, spec: dict[str, Any]) -> dict[str, Any] | None:
        with self.lock:
            with self.session_factory.begin() as session:
                model = session.get(ForwardingRuleModel, str(rule_id))
                if model is None:
                    return None
                if "name" in spec:
                    model.name = str(spec["name"])[:120]
                if "enabled" in spec:
                    model.enabled = bool(spec["enabled"])
                for key in ("match_device_id", "match_sender", "match_keyword", "template"):
                    if key in spec:
                        setattr(model, key, spec[key])
                if "channel" in spec:
                    model.channel = str(spec["channel"])
                if "match_phone" in spec:
                    model.match_phone_lookup_hash = (
                        self.crypto.lookup_digest(str(spec["match_phone"]), "receiving-phone:v1")
                        if spec.get("match_phone")
                        else None
                    )
                supplied_target = str(spec.get("target") or "").strip()
                if supplied_target:
                    target_value = self.crypto.encrypt_text(
                        supplied_target, f"forwarding-rule:{rule_id}:target:v1"
                    )
                    model.target_nonce = target_value.nonce
                    model.target_ciphertext = target_value.ciphertext
                    model.target_key_version = target_value.key_version
                model.updated_at = utc_now()
            return self.get_forwarding_rule(rule_id)

    def delete_forwarding_rule(self, rule_id: str) -> bool:
        with self.lock:
            with self.session_factory.begin() as session:
                model = session.get(ForwardingRuleModel, str(rule_id))
                if model is None:
                    return False
                session.delete(model)
                return True

    def record_forwarding_log(
        self,
        *,
        rule_id: str | None,
        rule_name: str,
        message_id: str | None,
        channel: str,
        target_masked: str,
        status: str,
        attempts: int,
        error: str | None = None,
    ) -> None:
        now = utc_now()
        with self.lock:
            with self.session_factory.begin() as session:
                session.add(
                    ForwardingLogModel(
                        rule_id=str(rule_id) if rule_id else None,
                        rule_name=str(rule_name)[:120],
                        message_id=str(message_id) if message_id else None,
                        channel=str(channel)[:16],
                        target_masked=str(target_masked)[:200],
                        status=str(status)[:16],
                        attempts=int(attempts),
                        error=str(error)[:500] if error else None,
                    )
                )
                # 日志保留 7 天：借写入时机低频清理（每 10 分钟最多一次）。
                global _FORWARDING_LOG_PRUNE_AT
                if now.timestamp() - _FORWARDING_LOG_PRUNE_AT > 600:
                    _FORWARDING_LOG_PRUNE_AT = now.timestamp()
                    session.execute(
                        delete(ForwardingLogModel).where(
                            ForwardingLogModel.created_at < now - timedelta(days=7)
                        )
                    )

    def list_forwarding_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            stmt = (
                select(ForwardingLogModel)
                .order_by(ForwardingLogModel.created_at.desc(), ForwardingLogModel.id.desc())
                .limit(max(1, min(int(limit), 200)))
            )
            return [
                {
                    "id": m.id,
                    "rule_id": m.rule_id,
                    "rule_name": m.rule_name,
                    "message_id": m.message_id,
                    "channel": m.channel,
                    "target_masked": m.target_masked,
                    "status": m.status,
                    "attempts": m.attempts,
                    "error": m.error,
                    "created_at": m.created_at,
                }
                for m in session.scalars(stmt).all()
            ]

    # ---------- 远程控制（设备指令队列，v1：查电量/查最近短信/触发立即上传） ----------

    def authenticate_device_public(
        self,
        *,
        device_id: str | None,
        timestamp: str | None,
        nonce: str | None,
        raw_body: bytes,
        signature: str | None,
    ) -> DeviceModel:
        """设备轮询/回传结果的公开鉴权（只读会话，不落 nonce、不写库）。

        与上报链路同一套 HMAC 规范（timestamp\\nnonce\\nsha256(body)），
        但命令轮询是幂等只读查询，不需要防重放表，避免无谓写放大。
        """
        with self.session_factory() as session:
            return self._authenticate_ingest_device(
                session,
                raw_body=raw_body,
                authorization=None,
                device_id=device_id,
                timestamp=timestamp,
                nonce=nonce,
                signature=signature,
            )

    def enqueue_device_command(self, device_id: UUID | str, command_type: str) -> dict[str, Any]:
        normalized = str(command_type or "").strip()
        if normalized not in DEVICE_COMMAND_TYPES:
            raise ValueError("unsupported command type")
        with self.lock:
            with self.session_factory.begin() as session:
                device = session.get(DeviceModel, str(device_id))
                if device is None:
                    raise KeyError("device_not_found")
                if not device.active:
                    raise ValueError("device_not_active")
                outstanding = session.scalar(
                    select(func.count())
                    .select_from(DeviceCommandModel)
                    .where(
                        DeviceCommandModel.device_id == str(device_id),
                        DeviceCommandModel.status.in_(("pending", "sent")),
                    )
                )
                if (outstanding or 0) >= DEVICE_COMMAND_QUEUE_LIMIT:
                    raise ValueError("command_queue_full")
                # 低频清理历史指令（与转发日志同款借用写入时机），保留 7 天
                global _DEVICE_COMMAND_PRUNE_AT
                now = utc_now()
                if now.timestamp() - _DEVICE_COMMAND_PRUNE_AT > 600:
                    _DEVICE_COMMAND_PRUNE_AT = now.timestamp()
                    session.execute(
                        delete(DeviceCommandModel).where(
                            DeviceCommandModel.created_at < now - timedelta(days=7)
                        )
                    )
                model = DeviceCommandModel(
                    id=str(uuid4()),
                    device_id=str(device_id),
                    command_type=normalized,
                    status="pending",
                )
                session.add(model)
                self._audit(
                    session,
                    "device_command.queued",
                    model.id,
                    {"device_id": str(device_id), "command_type": normalized},
                )
                return self._device_command_from_model(model)

    def take_pending_device_commands(self, device_id: UUID | str, limit: int = 10) -> list[dict[str, Any]]:
        """设备轮询取指令：返回可执行指令并置为 sent。

        除 pending 外，也会重新投递"陈旧 sent"（超过 DEVICE_COMMAND_REDELIVER_AFTER
        仍未回传结果，通常是设备取了指令后进程被杀）。v1 指令集全部幂等只读或可重复
        触发，重投不会产生副作用。
        """
        with self.lock:
            with self.session_factory.begin() as session:
                if session.get(DeviceModel, str(device_id)) is None:
                    raise KeyError("device_not_found")
                now = utc_now()
                stale_before = now - DEVICE_COMMAND_REDELIVER_AFTER
                models = session.scalars(
                    select(DeviceCommandModel)
                    .where(
                        DeviceCommandModel.device_id == str(device_id),
                        or_(
                            DeviceCommandModel.status == "pending",
                            and_(
                                DeviceCommandModel.status == "sent",
                                DeviceCommandModel.sent_at.is_not(None),
                                DeviceCommandModel.sent_at < stale_before,
                            ),
                        ),
                    )
                    .order_by(DeviceCommandModel.created_at, DeviceCommandModel.id)
                    .limit(max(1, min(int(limit), 20)))
                    .with_for_update()
                ).all()
                for model in models:
                    model.status = "sent"
                    model.sent_at = now
                return [self._device_command_from_model(m) for m in models]

    def complete_device_command(
        self,
        device_id: UUID | str,
        command_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        with self.lock:
            with self.session_factory.begin() as session:
                model = session.get(DeviceCommandModel, str(command_id))
                if model is None or model.device_id != str(device_id):
                    return None
                if model.status == "completed":
                    return self._device_command_from_model(model)
                encrypted = self.crypto.encrypt_text(
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    f"device-command:{model.id}:result:v1",
                )
                model.result_nonce = encrypted.nonce
                model.result_ciphertext = encrypted.ciphertext
                model.result_key_version = encrypted.key_version
                model.status = "completed"
                model.completed_at = utc_now()
                self._audit(
                    session,
                    "device_command.completed",
                    model.id,
                    {"device_id": str(device_id), "command_type": model.command_type},
                )
                return self._device_command_from_model(model)

    def list_device_commands(self, device_id: UUID | str, limit: int = 50) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            if session.get(DeviceModel, str(device_id)) is None:
                raise KeyError("device_not_found")
            models = session.scalars(
                select(DeviceCommandModel)
                .where(DeviceCommandModel.device_id == str(device_id))
                .order_by(DeviceCommandModel.created_at.desc(), DeviceCommandModel.id.desc())
                .limit(max(1, min(int(limit), 200)))
            ).all()
            return [self._device_command_from_model(m) for m in models]

    def _device_command_from_model(self, model: DeviceCommandModel) -> dict[str, Any]:
        result: dict[str, Any] | None = None
        if model.result_nonce and model.result_ciphertext:
            try:
                raw = self.crypto.decrypt_text(
                    EncryptedValue(
                        model.result_nonce,
                        model.result_ciphertext,
                        model.result_key_version or 1,
                    ),
                    f"device-command:{model.id}:result:v1",
                )
                parsed = json.loads(raw)
                result = parsed if isinstance(parsed, dict) else None
            except Exception:
                result = None
        return {
            "id": model.id,
            "device_id": model.device_id,
            "command_type": model.command_type,
            "status": model.status,
            "result": result,
            "created_at": model.created_at,
            "sent_at": model.sent_at,
            "completed_at": model.completed_at,
        }


_FORWARDING_LOG_PRUNE_AT = 0.0
_DEVICE_COMMAND_PRUNE_AT = 0.0


store = RelayStore()
