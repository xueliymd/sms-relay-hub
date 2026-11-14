import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import threading
import time
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Header, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse

from app.core.config import settings
from app.core.errors import RelayError
from app.core.redaction import mask_phone_number as _mask_phone_number
from app.core.redaction import scrub_metadata
from app.db.models import utc_now
from app.services.admin_message_websocket import (
    AdminMessageSubscription,
    admin_message_broker,
)
from app.services.forwarding import (
    FORWARDING_CHANNEL_LABELS,
    FORWARDING_CHANNELS,
    mask_forwarding_target,
    render_forwarding_content,
    validate_forwarding_target,
)
from app.services.forwarding import (
    forwarding_engine as forwarding_engine_instance,
)
from app.services.mqtt_contract import MqttContractError, parse_mqtt_envelope
from app.services.otp_websocket import OtpSubscription, otp_broker
from app.services.release_catalog import load_releases
from app.services.store import (
    SUPPORTED_DEVICE_CONTRACTS,
    DeviceSimProfile,
    IdempotencyConflict,
    InvalidDeviceCredential,
    normalize_phone_number,
    store,
)

# 转发引擎绑定存储（规则读取、日志写入、OTP 查询）。
forwarding_engine_instance.store = store

router = APIRouter(prefix="/api/v1", tags=["relay"])
MAX_INGEST_BODY_BYTES = 32 * 1024
MAX_WS_FRAME_BYTES = 8 * 1024
WS_AUTH_TIMEOUT_SECONDS = 10.0
WS_SUBSCRIPTION_TIMEOUT_SECONDS = 10.0
WS_IDLE_TIMEOUT_SECONDS = 180.0


class _FailedScriptAuthLimiter:
    """Small process-local lockout for repeated invalid script credentials.

    This intentionally only covers failed script-token authentication. It is
    not a request rate limiter and does not affect valid shortcut/module traffic.
    """

    def __init__(self, *, max_failures: int = 5, lock_seconds: int = 600) -> None:
        self.max_failures = max_failures
        self.lock_seconds = lock_seconds
        self._entries: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def is_locked(self, client_ip: str) -> bool:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(client_ip)
            if entry is None:
                return False
            failures, locked_until = entry
            if locked_until > now:
                return True
            if locked_until:
                self._entries.pop(client_ip, None)
            return False

    def record_failure(self, client_ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            failures, locked_until = self._entries.get(client_ip, (0, 0.0))
            if locked_until > now:
                return
            failures += 1
            self._entries[client_ip] = (
                failures,
                now + self.lock_seconds if failures >= self.max_failures else 0.0,
            )

    def reset(self, client_ip: str) -> None:
        with self._lock:
            self._entries.pop(client_ip, None)


script_auth_limiter = _FailedScriptAuthLimiter()


class _WebSocketFrameTooLarge(ValueError):
    pass


def _client_ip(connection: Request | WebSocket) -> str:
    client = connection.client
    host = getattr(client, "host", None)
    return host if isinstance(host, str) and host else "unknown"


def _script_token_from_authorization(authorization: str | None) -> str | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    return token or None


def _authenticate_script_token(token: str | None, client_ip: str) -> None:
    if script_auth_limiter.is_locked(client_ip):
        raise RelayError(429, "script_auth_locked", "Too many invalid script credentials from this IP")
    if token is None or not store.authenticate_api_client(token):
        script_auth_limiter.record_failure(client_ip)
        raise RelayError(401, "invalid_api_client", "Script API token is invalid")
    script_auth_limiter.reset(client_ip)


async def _receive_json_frame(
    websocket: WebSocket,
    timeout_seconds: float,
    max_frame_bytes: int | None = None,
) -> dict[str, Any]:
    limit = MAX_WS_FRAME_BYTES if max_frame_bytes is None else max_frame_bytes
    raw = await asyncio.wait_for(websocket.receive_text(), timeout=timeout_seconds)
    if len(raw.encode("utf-8")) > limit:
        raise _WebSocketFrameTooLarge()
    try:
        frame = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("frame must be valid JSON") from exc
    if not isinstance(frame, dict):
        raise ValueError("frame must be a JSON object")
    return frame

def require_admin(value: str | None) -> None:
    if not store.validate_admin_credential(value):
        raise RelayError(401, "admin_auth_required", "Admin token is invalid")


def _public_origin(request: Request) -> str:
    configured = settings.public_base_url.strip().rstrip("/")
    if configured:
        return configured
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    forwarded_host = request.headers.get("x-forwarded-host", "").split(",", 1)[0].strip()
    scheme = forwarded_proto or request.url.scheme
    host = forwarded_host or request.url.netloc
    return f"{scheme}://{host}".rstrip("/")


@router.post("/enrollment/consume")
async def consume_device_enrollment(
    payload: Any = Body(...),
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RelayError(422, "invalid_enrollment_request", "Request body must be an object")
    raw_device_id = payload.get("device_id")
    raw_token = payload.get("enrollment_token")
    if not isinstance(raw_device_id, str) or not isinstance(raw_token, str):
        raise RelayError(422, "invalid_enrollment_request", "device_id and enrollment_token are required")
    try:
        device_id = UUID(raw_device_id.strip())
    except ValueError:
        raise RelayError(422, "invalid_enrollment_request", "device_id is invalid") from None
    try:
        enrollment = store.consume_enrollment_token(device_id, raw_token)
    except ValueError as exc:
        error_code = str(exc)
        if error_code == "enrollment_token_expired":
            raise RelayError(410, error_code, "Enrollment QR code has expired") from exc
        if error_code == "device_enrollment_unavailable":
            raise RelayError(409, error_code, "Device is not available for enrollment") from exc
        raise RelayError(400, "invalid_enrollment_token", "Enrollment QR code is invalid or already used") from exc
    return {
        "device_id": str(enrollment.device_id),
        "device_name": enrollment.device_name,
        "client_type": enrollment.client_type,
        "auth_kind": enrollment.auth_kind,
        "hmac_secret": enrollment.hmac_secret,
    }


def require_mqtt_bridge(value: str | None) -> None:
    expected = settings.mqtt_bridge_token.strip()
    if not expected:
        raise RelayError(503, "mqtt_bridge_not_configured", "MQTT bridge is not configured")
    if not value or not value.lower().startswith("bearer "):
        raise RelayError(401, "mqtt_bridge_auth_required", "MQTT bridge token is required")
    token = value.split(" ", 1)[1].strip()
    if not hmac.compare_digest(token, expected):
        raise RelayError(401, "mqtt_bridge_auth_required", "MQTT bridge token is invalid")


SIM_PROFILE_SOURCES = {"auto", "manual", "vendor"}
POSTGRES_INTEGER_MIN = -(2**31)
POSTGRES_INTEGER_MAX = 2**31 - 1


def _script_token_from_frame(frame: Any) -> str:
    if not isinstance(frame, dict) or frame.get("type") != "auth":
        raise ValueError("first frame must be an auth frame")
    token = frame.get("token")
    if not isinstance(token, str) or not token.strip():
        raise ValueError("token is required")
    return token.strip()


def _subscription_from_frame(frame: Any) -> OtpSubscription:
    if not isinstance(frame, dict) or frame.get("type") != "subscribe":
        raise ValueError("subscribe frame is required")
    phone_number = frame.get("phone_number")
    if not isinstance(phone_number, str) or not phone_number.strip():
        raise ValueError("phone_number is required")
    device_id = frame.get("device_id")
    if device_id is not None:
        device_id = str(UUID(str(device_id)))
    sender = frame.get("sender")
    keyword = frame.get("keyword")
    if sender is not None and not isinstance(sender, str):
        raise ValueError("sender must be a string")
    if keyword is not None and not isinstance(keyword, str):
        raise ValueError("keyword must be a string")
    return OtpSubscription(
        phone_number=phone_number,
        device_id=device_id,
        sender=sender,
        keyword=keyword,
    )


def _admin_message_subscription_from_frame(frame: Any) -> AdminMessageSubscription:
    if not isinstance(frame, dict) or frame.get("type") != "subscribe":
        raise ValueError("subscribe frame is required")
    raw_device_id = frame.get("device_id")
    if raw_device_id is None:
        device_id = None
    else:
        if not isinstance(raw_device_id, str) or not raw_device_id.strip():
            raise ValueError("device_id must be a UUID or omitted for all devices")
        device_id = UUID(raw_device_id.strip())
    sim_slot = frame.get("sim_slot")
    if sim_slot is not None and (
        isinstance(sim_slot, bool) or not isinstance(sim_slot, int) or sim_slot < 0
    ):
        raise ValueError("sim_slot must be a non-negative integer")
    return AdminMessageSubscription(
        device_id=None if device_id is None else str(device_id),
        sim_slot=sim_slot,
    )


@router.websocket("/ws/otp")
async def otp_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    authenticated = False
    client_ip = _client_ip(websocket)
    try:
        try:
            auth_frame = await _receive_json_frame(websocket, WS_AUTH_TIMEOUT_SECONDS)
            token = _script_token_from_frame(auth_frame)
        except TimeoutError:
            await websocket.send_json({"type": "error", "code": "auth_timeout", "message": "Authentication frame timed out"})
            await websocket.close(code=1008)
            return
        except _WebSocketFrameTooLarge:
            await websocket.send_json({"type": "error", "code": "frame_too_large", "message": "WebSocket control frame is too large"})
            await websocket.close(code=1009)
            return
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            await websocket.send_json({"type": "error", "code": "invalid_auth", "message": str(exc)})
            await websocket.close(code=1008)
            return
        try:
            _authenticate_script_token(token, client_ip)
        except RelayError as exc:
            await websocket.send_json(
                {
                    "type": "error",
                    "code": exc.code,
                    "message": exc.message,
                }
            )
            await websocket.close(code=1008)
            return
        authenticated = True
        await websocket.send_json({"type": "auth_ok"})

        try:
            subscription_frame = await _receive_json_frame(
                websocket, WS_SUBSCRIPTION_TIMEOUT_SECONDS
            )
            subscription = _subscription_from_frame(subscription_frame)
        except TimeoutError:
            await websocket.send_json({"type": "error", "code": "subscription_timeout", "message": "Subscription frame timed out"})
            await websocket.close(code=1008)
            return
        except _WebSocketFrameTooLarge:
            await websocket.send_json({"type": "error", "code": "frame_too_large", "message": "WebSocket control frame is too large"})
            await websocket.close(code=1009)
            return
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            await websocket.send_json({"type": "error", "code": "invalid_subscription", "message": str(exc)})
            await websocket.close(code=1008)
            return
        await otp_broker.add(websocket, subscription)
        await websocket.send_json(
            {
                "type": "subscribed",
                "phone_number": subscription.phone_number,
                "device_id": subscription.device_id,
                "server_time": datetime.now(timezone.utc).isoformat(),
            }
        )
        while True:
            try:
                frame = await _receive_json_frame(websocket, WS_IDLE_TIMEOUT_SECONDS)
            except TimeoutError:
                await websocket.send_json({"type": "error", "code": "idle_timeout", "message": "WebSocket closed after 3 minutes without client activity"})
                await websocket.close(code=1000)
                return
            except _WebSocketFrameTooLarge:
                await websocket.send_json({"type": "error", "code": "frame_too_large", "message": "WebSocket control frame is too large"})
                await websocket.close(code=1009)
                return
            if frame.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            elif frame.get("type") == "subscribe":
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "subscription_already_set",
                        "message": "Only one subscription is allowed per connection",
                    }
                )
                await websocket.close(code=1008)
                return
    except WebSocketDisconnect:
        return
    finally:
        if authenticated:
            await otp_broker.remove(websocket)


@router.websocket("/ws/admin/messages")
async def admin_message_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    subscribed = False
    token: str | None = None
    try:
        try:
            auth_frame = await _receive_json_frame(websocket, WS_AUTH_TIMEOUT_SECONDS)
            token = _script_token_from_frame(auth_frame)
        except TimeoutError:
            await websocket.send_json(
                {
                    "type": "error",
                    "code": "auth_timeout",
                    "message": "Authentication frame timed out",
                }
            )
            await websocket.close(code=1008)
            return
        except _WebSocketFrameTooLarge:
            await websocket.send_json(
                {
                    "type": "error",
                    "code": "frame_too_large",
                    "message": "WebSocket control frame is too large",
                }
            )
            await websocket.close(code=1009)
            return
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            await websocket.send_json(
                {"type": "error", "code": "invalid_auth", "message": str(exc)}
            )
            await websocket.close(code=1008)
            return
        if not store.validate_admin_credential(token):
            await websocket.send_json(
                {
                    "type": "error",
                    "code": "admin_auth_required",
                    "message": "Admin token is invalid",
                }
            )
            await websocket.close(code=1008)
            return
        await websocket.send_json({"type": "auth_ok"})

        try:
            subscription_frame = await _receive_json_frame(
                websocket, WS_SUBSCRIPTION_TIMEOUT_SECONDS
            )
            subscription = _admin_message_subscription_from_frame(subscription_frame)
        except TimeoutError:
            await websocket.send_json(
                {
                    "type": "error",
                    "code": "subscription_timeout",
                    "message": "Subscription frame timed out",
                }
            )
            await websocket.close(code=1008)
            return
        except _WebSocketFrameTooLarge:
            await websocket.send_json(
                {
                    "type": "error",
                    "code": "frame_too_large",
                    "message": "WebSocket control frame is too large",
                }
            )
            await websocket.close(code=1009)
            return
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            await websocket.send_json(
                {
                    "type": "error",
                    "code": "invalid_subscription",
                    "message": str(exc),
                }
            )
            await websocket.close(code=1008)
            return

        if subscription.device_id is not None:
            device = store.get_device(UUID(subscription.device_id))
            if device is None or not device.active:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "device_not_active",
                        "message": "Device is missing or inactive",
                    }
                )
                await websocket.close(code=1008)
                return

        await admin_message_broker.add(websocket, subscription)
        subscribed = True
        await websocket.send_json(
            {
                "type": "subscribed",
                "device_id": subscription.device_id,
                "sim_slot": subscription.sim_slot,
                "server_time": datetime.now(timezone.utc).isoformat(),
            }
        )

        while True:
            try:
                frame = await _receive_json_frame(websocket, WS_IDLE_TIMEOUT_SECONDS)
            except TimeoutError:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "idle_timeout",
                        "message": "WebSocket closed after 3 minutes without client activity",
                    }
                )
                await websocket.close(code=1000)
                return
            except _WebSocketFrameTooLarge:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "frame_too_large",
                        "message": "WebSocket control frame is too large",
                    }
                )
                await websocket.close(code=1009)
                return

            if not store.validate_admin_credential(token):
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "admin_auth_required",
                        "message": "Admin session is no longer valid",
                    }
                )
                await websocket.close(code=1008)
                return
            if frame.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            elif frame.get("type") == "subscribe":
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "subscription_already_set",
                        "message": "Only one subscription is allowed per connection",
                    }
                )
                await websocket.close(code=1008)
                return
    except WebSocketDisconnect:
        return
    finally:
        if subscribed:
            await admin_message_broker.remove(websocket)


def _root_ingest_frame(frame: Any) -> dict[str, Any]:
    if not isinstance(frame, dict) or frame.get("type") != "message":
        raise ValueError("message frame is required")
    required = ("device_id", "timestamp", "nonce", "signature", "idempotency_key", "payload_b64")
    if not all(isinstance(frame.get(field), str) and frame[field].strip() for field in required):
        raise ValueError("device_id, timestamp, nonce, signature, idempotency_key and payload_b64 are required")
    payload_b64 = frame["payload_b64"]
    try:
        raw_body = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
        payload = json.loads(raw_body.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("payload_b64 is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    if payload.get("schema_version") != 1 or payload.get("source") != "root_module":
        raise ValueError("payload must use schema_version 1 and source root_module")
    if payload.get("client_message_id") != frame["idempotency_key"]:
        raise ValueError("idempotency key must match client_message_id")
    return {
        "device_id": frame["device_id"],
        "timestamp": frame["timestamp"],
        "nonce": frame["nonce"],
        "signature": frame["signature"],
        "idempotency_key": frame["idempotency_key"],
        "raw_body": raw_body,
        "payload": payload,
    }


@router.websocket("/ws/ingest")
async def root_ingest_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json({"type": "ready"})
    try:
        while True:
            try:
                frame = await _receive_json_frame(
                    websocket,
                    WS_IDLE_TIMEOUT_SECONDS,
                    max_frame_bytes=32 * 1024,
                )
            except TimeoutError:
                await websocket.close(code=1000)
                return
            except _WebSocketFrameTooLarge:
                await websocket.send_json(
                    {"type": "error", "code": "frame_too_large", "message": "Message frame is too large"}
                )
                await websocket.close(code=1009)
                return
            if frame.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            try:
                envelope = _root_ingest_frame(frame)
                payload = validate_ingest_payload(envelope["payload"])
                message, duplicate = store.ingest(
                    raw_body=envelope["raw_body"],
                    authorization=None,
                    device_id=envelope["device_id"],
                    timestamp=envelope["timestamp"],
                    nonce=envelope["nonce"],
                    signature=envelope["signature"],
                    idempotency_key=envelope["idempotency_key"],
                    payload=payload,
                    request_hash=hashlib.sha256(envelope["raw_body"]).hexdigest(),
                    source_address=_client_ip(websocket),
                    allow_nonce_replay_for_idempotent=True,
                )
            except InvalidDeviceCredential:
                await websocket.send_json(
                    {"type": "error", "accepted": False, "status_code": 401, "code": "invalid_device_token", "message": "Device credential is invalid, replayed, or revoked"}
                )
                continue
            except IdempotencyConflict:
                await websocket.send_json(
                    {"type": "error", "accepted": False, "status_code": 409, "code": "idempotency_conflict", "message": "Idempotency key was already used with a different request body"}
                )
                continue
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                await websocket.send_json(
                    {"type": "error", "accepted": False, "status_code": 422, "code": "invalid_message", "message": str(exc)}
                )
                continue
            if not duplicate:
                otp_event = store.get_otp_delivery(message.id)
                if otp_event:
                    await otp_broker.publish(otp_event)
                await _publish_admin_message(message)
                forwarding_engine_instance.schedule(message)
            await websocket.send_json(
                {
                    "type": "accepted",
                    "accepted": True,
                    "duplicate": duplicate,
                    "status_code": 200 if duplicate else 201,
                    "message_id": str(message.id),
                }
            )
    except WebSocketDisconnect:
        return


def _validate_sim_profile_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    allowed = {"subscription_id", "phone_number", "carrier_name", "source"}
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError("unsupported SIM profile field")
    if "subscription_id" in payload and payload["subscription_id"] is not None:
        if isinstance(payload["subscription_id"], bool) or not isinstance(payload["subscription_id"], int):
            raise ValueError("subscription_id must be an integer or null")
        if not POSTGRES_INTEGER_MIN <= payload["subscription_id"] <= POSTGRES_INTEGER_MAX:
            raise ValueError("subscription_id is outside the supported integer range")
    validated = dict(payload)
    for field_name in ("phone_number", "carrier_name"):
        if field_name in payload and payload[field_name] is not None:
            if not isinstance(payload[field_name], str):
                raise ValueError(f"{field_name} must be a string or null")
            if field_name == "phone_number":
                validated[field_name] = normalize_phone_number(payload[field_name])
            elif len(payload[field_name]) > 160:
                raise ValueError(f"{field_name} is too long")
    if "source" in payload and (
        not isinstance(payload["source"], str) or payload["source"] not in SIM_PROFILE_SOURCES
    ):
        raise ValueError("source must be one of auto, manual, vendor")
    return validated


def _validate_sim_slot(slot: int) -> None:
    if slot < 0 or slot > POSTGRES_INTEGER_MAX:
        raise RelayError(422, "invalid_sim_profile", "slot_index is outside the supported range")


def _serialize_message(message: Any, *, otp_code: str | None = None) -> dict[str, Any]:
    subscription_id = message.metadata.get("subscription_id")
    if not isinstance(subscription_id, int) or isinstance(subscription_id, bool):
        subscription_id = None
    message_date = message.metadata.get("message_date")
    if not isinstance(message_date, str):
        message_date = None
    return {
        "id": str(message.id),
        "device_id": str(message.device_id),
        "client_message_id": message.client_message_id,
        "received_at": message.received_at.isoformat(),
        "created_at": message.created_at.isoformat(),
        "message_date": message_date,
        "sender": message.sender,
        "body": None if message.redacted else message.body,
        "source": message.source,
        "sim_slot": message.sim_slot,
        "subscription_id": subscription_id,
        "receiving_phone_masked": _mask_phone_number(message.receiving_phone),
        "receiving_identity_status": message.receiving_identity_status,
        "otp_status": message.otp_status,
        "otp_code": otp_code,
        "read_at": message.read_at.isoformat() if message.read_at else None,
        "metadata": scrub_metadata(message.metadata) if isinstance(message.metadata, Mapping) else {},
        "redacted": message.redacted,
    }


def _serialize_message_with_otp(message: Any) -> dict[str, Any]:
    """Serialize one message together with its OTP state.

    Uses the same lookup rules as the list endpoint, so a REST backfill and the
    live WebSocket event carry identical values for the same message (a reconnect
    no longer drops the OTP that the live event carried). ``otp_status`` has to be
    filled here too: store.ingest() returns a Message whose otp_status is unset
    (only the list/single-message queries populate it), so without this the live
    event would always say ``null`` while REST said ``available``.
    """
    state = store.get_otp_states([message.id]).get(str(message.id)) or {}
    event = _serialize_message(message, otp_code=state.get("code"))
    event["otp_status"] = state.get("status")
    return event


async def _publish_admin_message(message: Any) -> None:
    event = _serialize_message_with_otp(message)
    event["type"] = "message"
    event["cursor"] = {"created_at": event["created_at"], "id": event["id"]}
    await admin_message_broker.publish(event)


def _serialize_sim_profile(profile: DeviceSimProfile) -> dict[str, Any]:
    return {
        "device_id": str(profile.device_id),
        "slot_index": profile.slot_index,
        "subscription_id": profile.subscription_id,
        "phone_number_masked": _mask_phone_number(profile.phone_number),
        "carrier_name": profile.carrier_name,
        "source": profile.source,
        "updated_at": profile.updated_at.isoformat(),
    }


def _serialize_device(device: Any) -> dict[str, Any]:
    online_cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=settings.device_online_window_seconds
    )
    return {
        "id": str(device.id),
        "name": device.name,
        "client_type": device.client_type,
        "auth_kind": device.auth_kind,
        "active": device.active,
        "online": bool(
            device.active
            and device.last_seen_at is not None
            and device.last_seen_at >= online_cutoff
        ),
        "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
        "client_instance_id": device.client_instance_id,
        "client_version": device.client_version,
        "client_mode": device.client_mode,
        "runtime_updated_at": (
            device.runtime_updated_at.isoformat() if device.runtime_updated_at else None
        ),
        "credential_reuse_warning": device.credential_reuse_warning,
        "duplicate_collection_warning": device.duplicate_collection_warning,
    }


def _admin_session_response(account_id: str, username: str, session_version: int) -> dict[str, Any]:
    token, expires_at = store.issue_admin_session(account_id, username, session_version)
    return {
        "token": token,
        "username": username,
        "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
    }


@router.post("/admin/login")
async def admin_login(payload: Any = Body(...)) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RelayError(422, "invalid_admin_login", "Request body must be an object")
    username = payload.get("username")
    password = payload.get("password")
    if not isinstance(username, str) or not isinstance(password, str):
        raise RelayError(422, "invalid_admin_login", "Username and password are required")
    account = store.authenticate_admin(username.strip(), password)
    if account is None:
        raise RelayError(401, "admin_auth_required", "Username or password is invalid")
    return _admin_session_response(*account)


@router.put("/admin/account")
async def update_admin_account(
    payload: Any = Body(...),
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin(x_admin_token)
    if not isinstance(payload, dict):
        raise RelayError(422, "invalid_admin_account", "Request body must be an object")
    current_password = payload.get("current_password")
    username = payload.get("username")
    password = payload.get("password")
    if not isinstance(current_password, str):
        raise RelayError(422, "invalid_admin_account", "Current password is required")
    if username is not None and not isinstance(username, str):
        raise RelayError(422, "invalid_admin_account", "Username must be a string")
    if password is not None and not isinstance(password, str):
        raise RelayError(422, "invalid_admin_account", "Password must be a string")
    try:
        account = store.update_admin_account(
            current_password=current_password,
            username=username,
            password=password,
        )
    except PermissionError as exc:
        raise RelayError(401, "admin_auth_required", str(exc)) from exc
    except ValueError as exc:
        raise RelayError(422, "invalid_admin_account", str(exc)) from exc
    return _admin_session_response(*account)


@router.post("/admin/devices", status_code=201)
async def create_device(payload: Any = Body(...), x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    try:
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        name = payload.get("name", "Unnamed device")
        client_type = payload["client_type"]
        auth_kind = payload["auth_kind"]
        if not isinstance(name, str):
            raise ValueError("name must be a string")
        if not isinstance(client_type, str):
            raise ValueError("client_type must be a string")
        if not isinstance(auth_kind, str):
            raise ValueError("auth_kind must be a string")
        expected = SUPPORTED_DEVICE_CONTRACTS.get(client_type)
        if expected is None or expected[0] != auth_kind:
            raise ValueError("client_type and auth_kind are not a supported pair")
        device, credential = store.create_device(name, client_type, auth_kind)
    except (KeyError, ValueError) as exc:
        raise RelayError(422, "invalid_device", str(exc)) from exc
    return {"device": {"id": str(device.id), "name": device.name, "client_type": device.client_type, "auth_kind": device.auth_kind, "active": True}, "credential": {"type": device.auth_kind, "secret": credential}}


@router.post("/admin/devices/{device_id}/enrollment-qr")
async def create_device_enrollment_qr(
    device_id: UUID,
    request: Request,
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin(x_admin_token)
    try:
        ticket = store.create_enrollment_token(device_id)
    except KeyError as exc:
        raise RelayError(404, "device_not_found", "Device not found") from exc
    except ValueError as exc:
        if str(exc) == "device_not_active":
            raise RelayError(409, "device_not_active", "Only active devices can create an enrollment QR code") from exc
        raise RelayError(422, "device_enrollment_unsupported", "Only Android app or Root HMAC devices support QR enrollment") from exc
    device = store.get_device(device_id)
    if device is None:
        raise RelayError(404, "device_not_found", "Device not found")
    qr_payload = {
        "v": 1,
        "type": "sms-relay-enroll",
        "url": _public_origin(request),
        "device_id": str(ticket.device_id),
        "token": ticket.token,
        "expires_at": ticket.expires_at.isoformat(),
    }
    return {
        "device": {
            "id": str(device.id),
            "name": device.name,
            "client_type": device.client_type,
            "auth_kind": device.auth_kind,
        },
        "qr_text": json.dumps(qr_payload, ensure_ascii=False, separators=(",", ":")),
        "expires_at": ticket.expires_at.isoformat(),
        "ttl_seconds": 300,
    }


@router.get("/admin/devices")
async def list_devices(x_admin_token: str | None = Header(default=None)) -> list[dict[str, Any]]:
    require_admin(x_admin_token)
    return [_serialize_device(device) for device in store.list_devices()]


def _latest_releases() -> dict[str, dict[str, Any]]:
    """每个平台 stable 通道的最新一条发布记录（供 /admin/runtime 展示）。

    该端点是状态探针：发布清单缺失或损坏时必须降级成空字典，不能因此 500。
    """
    try:
        releases = load_releases()
    except Exception:
        return {}
    latest: dict[str, dict[str, Any]] = {}
    for release in releases:
        if release["channel"] != "stable":
            continue
        platform = release["platform"]
        current = latest.get(platform)
        if current is not None and current["version_code"] >= release["version_code"]:
            continue
        latest[platform] = {
            "version_code": release["version_code"],
            "version_name": release["version_name"],
            "published_at": release["published_at"],
            "download_url": release["download_url"],
            "sha256": release["sha256"],
        }
    return latest


@router.get("/admin/runtime")
async def runtime_identity(
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin(x_admin_token)
    try:
        store.check_database()
        database_status = "healthy"
    except Exception:
        database_status = "unavailable"
    return {
        "database_backend": store.engine.dialect.name,
        "database_fingerprint": store.database_fingerprint(),
        "platform_version": settings.platform_version,
        # 最新 APK / Root / 部署包版本：直接取发布清单，避免与 catalog 手写副本漂移。
        "latest_releases": _latest_releases(),
        # 证书由上游反代（宝塔 nginx）终结，容器内不再持有证书，因此这里恒为 null；
        # 保留该字段是为了让客户端能区分"不适用"与"后端漏返回"，不要据此判断链路是否加密。
        # 链路加密请用 https_enabled + public_base_url。
        "certificate_expires_at": None,
        "public_base_url": settings.public_base_url,
        "relay_site": settings.relay_site,
        "http_port": settings.http_port,
        "https_port": settings.https_port,
        "app_port": settings.app_port,
        "https_enabled": settings.public_base_url.startswith("https://"),
        "services": {
            "app": "healthy",
            "database": database_status,
            "caddy": "configured" if settings.relay_site else "not_configured",
        },
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/admin/devices/{device_id}/rotate-credential")
async def rotate_device(device_id: UUID, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    try:
        device, credential = store.rotate_device(device_id)
    except KeyError as exc:
        raise RelayError(404, "device_not_found", "Device not found") from exc
    except ValueError as exc:
        raise RelayError(409, "device_not_active", "Only active devices can rotate credentials") from exc
    return {"device": {"id": str(device.id), "active": device.active}, "credential": {"type": device.auth_kind, "secret": credential}}


@router.post("/admin/devices/{device_id}/restore")
async def restore_device(device_id: UUID, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    try:
        device, credential = store.restore_device(device_id)
    except KeyError as exc:
        raise RelayError(404, "device_not_found", "Device not found") from exc
    except ValueError as exc:
        raise RelayError(409, "device_not_revoked", "Only revoked devices can be restored") from exc
    return {"device": {"id": str(device.id), "active": device.active}, "credential": {"type": device.auth_kind, "secret": credential}}


@router.delete("/admin/devices/{device_id}/purge", status_code=204)
async def purge_device(device_id: UUID, x_admin_token: str | None = Header(default=None)) -> None:
    require_admin(x_admin_token)
    try:
        store.purge_device(device_id)
    except KeyError as exc:
        raise RelayError(404, "device_not_found", "Device not found") from exc
    except ValueError as exc:
        raise RelayError(409, "device_not_revoked", "Only revoked devices can be purged") from exc


@router.get("/admin/devices/{device_id}/sim-profiles")
async def list_sim_profiles(
    device_id: UUID, x_admin_token: str | None = Header(default=None)
) -> list[dict[str, Any]]:
    require_admin(x_admin_token)
    try:
        profiles = store.list_sim_profiles(device_id)
    except KeyError as exc:
        raise RelayError(404, "device_not_found", "Device not found") from exc
    return [_serialize_sim_profile(profile) for profile in profiles]


@router.get("/admin/devices/{device_id}/sim-profiles/{slot}")
async def get_sim_profile(
    device_id: UUID, slot: int, x_admin_token: str | None = Header(default=None)
) -> dict[str, Any]:
    require_admin(x_admin_token)
    _validate_sim_slot(slot)
    try:
        profile = store.get_sim_profile(device_id, slot)
    except KeyError as exc:
        raise RelayError(404, "device_not_found", "Device not found") from exc
    if profile is None:
        raise RelayError(404, "sim_profile_not_found", "SIM profile not found")
    return _serialize_sim_profile(profile)


@router.put("/admin/devices/{device_id}/sim-profiles/{slot}")
async def upsert_sim_profile(
    device_id: UUID,
    slot: int,
    payload: Any = Body(...),
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin(x_admin_token)
    _validate_sim_slot(slot)
    try:
        updates = _validate_sim_profile_payload(payload)
        profile = store.upsert_sim_profile(device_id, slot, updates)
    except KeyError as exc:
        raise RelayError(404, "device_not_found", "Device not found") from exc
    except ValueError as exc:
        raise RelayError(422, "invalid_sim_profile", str(exc)) from exc
    return _serialize_sim_profile(profile)


@router.delete("/admin/devices/{device_id}/sim-profiles/{slot}", status_code=204)
async def delete_sim_profile(
    device_id: UUID, slot: int, x_admin_token: str | None = Header(default=None)
) -> None:
    require_admin(x_admin_token)
    _validate_sim_slot(slot)
    try:
        store.delete_sim_profile(device_id, slot)
    except KeyError as exc:
        raise RelayError(404, "device_not_found", "Device not found") from exc
    except LookupError as exc:
        raise RelayError(404, "sim_profile_not_found", "SIM profile not found") from exc


@router.delete("/admin/devices/{device_id}", status_code=204)
async def revoke_device(device_id: UUID, x_admin_token: str | None = Header(default=None)) -> None:
    require_admin(x_admin_token)
    try:
        store.revoke_device(device_id)
    except KeyError:
        raise RelayError(404, "device_not_found", "Device not found")


# ---------- 远程控制 v1（管理端下发指令 / 设备轮询执行并回传结果） ----------


@router.post("/admin/devices/{device_id}/commands", status_code=201)
async def create_device_command(
    device_id: UUID,
    payload: Any = Body(...),
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin(x_admin_token)
    if not isinstance(payload, dict):
        raise RelayError(422, "invalid_command", "Request body must be an object")
    command_type = payload.get("command_type")
    if not isinstance(command_type, str):
        raise RelayError(422, "invalid_command", "command_type is required")
    try:
        return store.enqueue_device_command(device_id, command_type)
    except KeyError as exc:
        raise RelayError(404, "device_not_found", "Device not found") from exc
    except ValueError as exc:
        if str(exc) == "device_not_active":
            raise RelayError(409, "device_not_active", "Only active devices accept commands") from exc
        if str(exc) == "command_queue_full":
            raise RelayError(
                429,
                "command_queue_full",
                "Too many outstanding commands; wait for the device to execute them",
            ) from exc
        if str(exc) == "command_not_supported":
            raise RelayError(
                422,
                "command_not_supported",
                "This client type has no persistent command poller",
            ) from exc
        raise RelayError(422, "invalid_command", str(exc)) from exc


@router.get("/admin/devices/{device_id}/commands")
async def list_device_commands(
    device_id: UUID,
    limit: int = 50,
    x_admin_token: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    require_admin(x_admin_token)
    try:
        return store.list_device_commands(device_id, limit=limit)
    except KeyError as exc:
        raise RelayError(404, "device_not_found", "Device not found") from exc


@router.get("/devices/{device_id}/commands/pending")
async def device_pending_commands(
    device_id: UUID,
    x_device_id: str | None = Header(default=None),
    x_timestamp: str | None = Header(default=None),
    x_nonce: str | None = Header(default=None),
    x_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    """设备轮询待执行指令。HMAC 鉴权（对空 body 签名），路径 ID 必须与凭据一致。"""
    _authenticate_command_device(
        raw_body=b"",
        path_device_id=str(device_id),
        device_id=x_device_id,
        timestamp=x_timestamp,
        nonce=x_nonce,
        signature=x_signature,
    )
    try:
        commands = store.take_pending_device_commands(device_id)
    except KeyError as exc:
        raise RelayError(404, "device_not_found", "Device not found") from exc
    return {"commands": commands}


@router.post("/devices/{device_id}/commands/{command_id}/result")
async def device_command_result(
    device_id: UUID,
    command_id: UUID,
    request: Request,
    authorization: str | None = Header(default=None),
    x_device_id: str | None = Header(default=None),
    x_timestamp: str | None = Header(default=None),
    x_nonce: str | None = Header(default=None),
    x_signature: str | None = Header(default=None),
) -> dict[str, Any]:
    """设备回传指令执行结果（结果加密落库）。"""
    raw = await read_ingest_body(request)
    _authenticate_command_device(
        raw_body=raw,
        path_device_id=str(device_id),
        device_id=x_device_id,
        timestamp=x_timestamp,
        nonce=x_nonce,
        signature=x_signature,
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RelayError(422, "invalid_command_result", "Request body must be JSON") from exc
    if not isinstance(payload, dict):
        raise RelayError(422, "invalid_command_result", "Request body must be an object")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RelayError(422, "invalid_command_result", "result must be an object")
    if len(json.dumps(result, separators=(",", ":")).encode()) > 16384:
        raise RelayError(422, "invalid_command_result", "result is too large")
    try:
        completed = store.complete_device_command(device_id, str(command_id), result)
    except ValueError as exc:
        if str(exc) == "command_expired":
            raise RelayError(409, "command_expired", "Command expired before its result arrived") from exc
        raise
    if completed is None:
        raise RelayError(404, "command_not_found", "Command not found for this device")
    return {"accepted": True, "status": completed["status"]}


def _authenticate_command_device(
    *,
    raw_body: bytes,
    path_device_id: str,
    device_id: str | None,
    timestamp: str | None,
    nonce: str | None,
    signature: str | None,
) -> None:
    """设备下行链路鉴权：HMAC 校验 + 路径设备 ID 与凭据必须一致。"""
    try:
        model = store.authenticate_device_public(
            device_id=device_id,
            timestamp=timestamp,
            nonce=nonce,
            raw_body=raw_body,
            signature=signature,
        )
    except InvalidDeviceCredential as exc:
        raise RelayError(401, "invalid_device_token", "Device credential is invalid or revoked") from exc
    if str(model.id) != path_device_id:
        raise RelayError(403, "device_mismatch", "Device credential does not match the requested device")


def validate_ingest_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("message body must be a JSON object")
    if payload.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    client_message_id = payload.get("client_message_id")
    sender = payload.get("sender")
    body = payload.get("body")
    metadata = payload.get("metadata", {})
    if not isinstance(client_message_id, str) or not client_message_id or len(client_message_id) > 200:
        raise ValueError("client_message_id must contain 1 to 200 characters")
    if not isinstance(sender, str) or not sender or len(sender) > 200:
        raise ValueError("sender must contain 1 to 200 characters")
    if not isinstance(body, str) or not body or len(body) > 4096:
        raise ValueError("body must contain 1 to 4096 characters")
    if not isinstance(metadata, dict) or len(json.dumps(metadata, separators=(",", ":")).encode()) > 8192:
        raise ValueError("metadata must be a JSON object no larger than 8192 bytes")
    received_at = datetime.fromisoformat(str(payload.get("received_at", "")).replace("Z", "+00:00"))
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError("received_at must include a timezone")
    payload["received_at"] = received_at
    return payload


def _shortcut_field(message: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in message:
            return message[name]
    lowered = {str(key).lower(): value for key, value in message.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _normalize_shortcut_message_date(value: Any, fallback: str, fallback_source: str) -> tuple[str, str]:
    candidate: datetime | None = None
    if isinstance(value, datetime):
        candidate = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            timestamp = float(value)
            if abs(timestamp) >= 100_000_000_000:
                timestamp /= 1000
            candidate = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            candidate = None
    elif isinstance(value, str):
        normalized = value.strip()
        if normalized:
            try:
                if normalized.isdigit():
                    timestamp = float(normalized)
                    if abs(timestamp) >= 100_000_000_000:
                        timestamp /= 1000
                    candidate = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                else:
                    candidate = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            except (OverflowError, OSError, ValueError):
                candidate = None
    if candidate is None or candidate.tzinfo is None or candidate.utcoffset() is None:
        return fallback, fallback_source
    return candidate.astimezone(timezone.utc).isoformat(), "device"


def _parse_shortcut_received_at(value: Any) -> datetime | None:
    candidate: datetime | None = None
    if isinstance(value, datetime):
        candidate = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            timestamp = float(value)
            if abs(timestamp) >= 100_000_000_000:
                timestamp /= 1000
            candidate = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            candidate = None
    elif isinstance(value, str) and value.strip():
        try:
            candidate = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            candidate = None
    if candidate is None or candidate.tzinfo is None or candidate.utcoffset() is None:
        return None
    return candidate.astimezone(timezone.utc)


def build_shortcut_payload(
    message: Any,
    *,
    received_at: datetime | None = None,
) -> dict[str, Any]:
    """Convert an iOS ExtensionInput into the existing ingest contract."""
    now = received_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("received_at must include a timezone")
    if isinstance(message, list):
        if len(message) != 1:
            raise ValueError("message must contain exactly one item")
        message = message[0]

    sender = "iPhone Messages"
    body: Any = message
    # 服务器分钟只作为没有设备消息时间时的去重回退桶。设备消息里的 Date
    # 稳定且随短信一起重发，不能依赖 received_at（快捷指令重试时会变）。
    server_minute = datetime.now(timezone.utc).replace(second=0, microsecond=0).isoformat()
    # 展示用的短信时间优先级：设备消息里的 Date 字段 > 请求的 received_at
    # （iOS 设备时间）> 服务器分钟。配置了 received_at 的快捷指令将显示
    # 设备时间，而不是服务器生成时间。
    message_date = (
        now.replace(second=0, microsecond=0).isoformat()
        if received_at is not None
        else server_minute
    )
    message_date_source = "device_received_at" if received_at is not None else "server_receive_minute"
    phone_value: Any = None
    if isinstance(message, dict):
        sender_value = _shortcut_field(
            message,
            "Sender",
            "sender",
            "From",
            "from",
            "\u53d1\u4ef6\u4eba",
        )
        body = _shortcut_field(
            message,
            "Content",
            "content",
            "Body",
            "body",
            "Text",
            "text",
            "Message",
            "message",
            "\u5185\u5bb9",
            "\u4fe1\u606f",
        )
        date_value = _shortcut_field(
            message,
            "Date",
            "date",
            "MessageDate",
            "message_date",
            "\u65e5\u671f",
        )
        phone_value = _shortcut_field(
            message,
            "PhoneNumber",
            "phone_number",
            "RecipientPhoneNumber",
            "recipient_phone_number",
            "To",
            "to",
            "\u63a5\u6536\u53f7\u7801",
            "\u624b\u673a\u53f7\u7801",
        )
        if isinstance(sender_value, str) and sender_value.strip():
            sender = sender_value.strip()
        message_date, message_date_source = _normalize_shortcut_message_date(
            date_value,
            message_date,
            message_date_source,
        )
    if not isinstance(body, str) or not body.strip():
        raise ValueError("message content must be a non-empty string")
    body = body.strip()
    # 设备消息时间存在时，用 Date 作为稳定去重锚点；只有缺失/无效 Date
    # 才回退到服务器分钟。这样同一条带 Date 的短信跨分钟重发仍是同一个
    # client_message_id，而服务器分钟桶也保留对普通文本的有限防重能力。
    idempotency_bucket = message_date if message_date_source == "device" else server_minute
    canonical = f"ios-shortcut-v1\n{sender}\n{idempotency_bucket}\n{body}"
    client_message_id = "ios-shortcut-v1:" + hashlib.sha256(canonical.encode()).hexdigest()
    metadata: dict[str, Any] = {
        "shortcut_version": "1.0.0",
        "ingest_mode": "shortcut",
        "message_date": message_date,
        "message_date_source": message_date_source,
    }
    # Newer iOS versions may include the receiving line in Shortcut Input;
    # older versions expose only sender/content/date. This is optional
    # enrichment and must never block accepting the SMS.
    if isinstance(phone_value, str):
        try:
            metadata["phone_number"] = normalize_phone_number(phone_value)
        except ValueError:
            pass
    return {
        "schema_version": 1,
        "client_message_id": client_message_id,
        "received_at": now.isoformat(),
        "sender": sender,
        "body": body,
        "source": "ios_shortcut",
        "metadata": metadata,
    }


async def read_ingest_body(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_INGEST_BODY_BYTES:
                raise RelayError(413, "request_too_large", "Request body is too large")
        except ValueError:
            raise RelayError(400, "invalid_content_length", "Content-Length is invalid") from None

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_INGEST_BODY_BYTES:
            raise RelayError(413, "request_too_large", "Request body is too large")
    return bytes(body)


@router.get("/admin/forwarding/rules")
async def list_forwarding_rules(x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    rules = await asyncio.to_thread(store.list_forwarding_rules)
    return {
        "channels": [
            {"id": channel, "label": FORWARDING_CHANNEL_LABELS[channel]}
            for channel in FORWARDING_CHANNELS
        ],
        "rules": [public_forwarding_rule(rule) for rule in rules],
    }


@router.post("/admin/forwarding/rules", status_code=201)
async def create_forwarding_rule(payload: Any = Body(...), x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    try:
        spec = normalize_forwarding_spec(payload, require_target=True)
    except ValueError as exc:
        raise RelayError(422, "invalid_forwarding_rule", str(exc)) from exc
    rule = await asyncio.to_thread(store.create_forwarding_rule, spec)
    return {"rule": public_forwarding_rule(rule)}


@router.put("/admin/forwarding/rules/{rule_id}")
async def update_forwarding_rule(rule_id: str, payload: Any = Body(...), x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    try:
        spec = normalize_forwarding_spec(payload, require_target=False)
    except ValueError as exc:
        raise RelayError(422, "invalid_forwarding_rule", str(exc)) from exc
    rule = await asyncio.to_thread(store.update_forwarding_rule, rule_id, spec)
    if rule is None:
        raise RelayError(404, "forwarding_rule_not_found", "Forwarding rule does not exist")
    return {"rule": public_forwarding_rule(rule)}


@router.delete("/admin/forwarding/rules/{rule_id}")
async def delete_forwarding_rule(rule_id: str, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    deleted = await asyncio.to_thread(store.delete_forwarding_rule, rule_id)
    if not deleted:
        raise RelayError(404, "forwarding_rule_not_found", "Forwarding rule does not exist")
    return {"deleted": True}


@router.post("/admin/forwarding/rules/{rule_id}/test")
async def test_forwarding_rule(rule_id: str, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    rule = await asyncio.to_thread(store.get_forwarding_rule, rule_id)
    if rule is None:
        raise RelayError(404, "forwarding_rule_not_found", "Forwarding rule does not exist")
    from app.services.forwarding import send_to_channel

    variables = {
        "sender": "10086",
        "body": "【测试】这是一条来自转发规则的测试消息。",
        "phone": "（按规则匹配的接收号码）",
        "code": "123456",
        "received_at": utc_now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    title = f"【短信转发】{variables['sender']}"
    content = render_forwarding_content(rule.get("template"), variables)
    ok, status_code, message = await asyncio.to_thread(
        send_to_channel,
        rule.get("channel") or "wecom",
        rule.get("target") or "",
        title,
        content,
        timeout=10,
    )
    await asyncio.to_thread(
        store.record_forwarding_log,
        rule_id=rule.get("id"),
        rule_name=str(rule.get("name") or ""),
        message_id=None,
        channel=str(rule.get("channel") or "wecom"),
        target_masked=mask_forwarding_target(str(rule.get("target") or "")),
        status="success" if ok else "failed",
        attempts=1,
        error=None if ok else message,
    )
    return {"ok": ok, "status_code": status_code, "message": message, "content": content}


@router.get("/admin/forwarding/logs")
async def list_forwarding_logs(limit: int = 50, x_admin_token: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(x_admin_token)
    logs = await asyncio.to_thread(store.list_forwarding_logs, limit)
    return {"logs": logs}


def public_forwarding_rule(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rule["id"],
        "name": rule["name"],
        "enabled": rule["enabled"],
        "match_device_id": rule["match_device_id"],
        "match_phone_masked": (
            f"{rule['match_phone_lookup_hash'][:12]}…"
            if rule.get("match_phone_lookup_hash")
            else ""
        ),
        "match_sender": rule.get("match_sender") or "",
        "match_keyword": rule.get("match_keyword") or "",
        "channel": rule["channel"],
        "target_masked": mask_forwarding_target(rule.get("target") or ""),
        "template": rule.get("template") or "",
        "created_at": rule["created_at"].isoformat() if rule.get("created_at") else "",
        "updated_at": rule["updated_at"].isoformat() if rule.get("updated_at") else "",
    }


def normalize_forwarding_spec(payload: Any, *, require_target: bool) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    channel = str(payload.get("channel") or "wecom").strip().lower()
    target = str(payload.get("target") or "").strip()
    if require_target or target:
        target = validate_forwarding_target(channel, target)
    spec: dict[str, Any] = {
        "name": name,
        "enabled": bool(payload.get("enabled", True)),
        "channel": channel,
        "target": target,
    }
    match_device_id = str(payload.get("match_device_id") or "").strip()
    if match_device_id:
        spec["match_device_id"] = match_device_id
    match_phone = str(payload.get("match_phone") or "").strip()
    if match_phone:
        try:
            spec["match_phone"] = normalize_phone_number(match_phone)
        except (TypeError, ValueError) as exc:
            raise ValueError("match_phone is invalid") from exc
    match_sender = str(payload.get("match_sender") or "").strip()
    if match_sender:
        spec["match_sender"] = match_sender[:120]
    match_keyword = str(payload.get("match_keyword") or "").strip()
    if match_keyword:
        spec["match_keyword"] = match_keyword[:200]
    template = payload.get("template")
    if template is not None:
        if not isinstance(template, str):
            raise ValueError("template must be a string")
        if len(template) > 2000:
            raise ValueError("template is too long")
        spec["template"] = template
    return spec


@router.post("/ingest/shortcut")
async def ingest_shortcut(
    request: Request,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    raw = await read_ingest_body(request)
    try:
        request_payload = json.loads(raw)
        if not isinstance(request_payload, dict) or "message" not in request_payload:
            raise ValueError("message is required")
        device_received_at = _parse_shortcut_received_at(request_payload.get("received_at"))
        payload = validate_ingest_payload(
            build_shortcut_payload(request_payload["message"], received_at=device_received_at)
        )
        canonical_raw = json.dumps(
            {**payload, "received_at": payload["received_at"].isoformat()},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        # 幂等请求哈希必须排除展示时间字段（message_date / message_date_source
        # 随 received_at 变化）——否则同一短信重发时同 id 不同哈希 → 409 冲突，
        # 或哈希碰撞时误判为冲突。展示时间只影响显示，不影响请求同一性。
        idempotent_metadata = {
            key: value
            for key, value in payload["metadata"].items()
            if key not in ("message_date", "message_date_source")
        }
        stable_request = json.dumps(
            {
                "client_message_id": payload["client_message_id"],
                "sender": payload["sender"],
                "body": payload["body"],
                "source": payload["source"],
                "metadata": idempotent_metadata,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        message, duplicate = store.ingest(
            raw_body=canonical_raw,
            authorization=authorization,
            device_id=None,
            timestamp=None,
            nonce=None,
            signature=None,
            idempotency_key=payload["client_message_id"],
            payload=payload,
            request_hash=hashlib.sha256(stable_request).hexdigest(),
            source_address=_client_ip(request),
        )
    except InvalidDeviceCredential as exc:
        raise RelayError(
            401,
            "invalid_device_token",
            "Device credential is invalid, replayed, or revoked",
        ) from exc
    except IdempotencyConflict as exc:
        raise RelayError(
            409,
            "idempotency_conflict",
            "Idempotency key was already used with a different request body",
        ) from exc
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RelayError(422, "invalid_message", str(exc)) from exc
    if not duplicate:
        otp_event = store.get_otp_delivery(message.id)
        if otp_event:
            await otp_broker.publish(otp_event)
        await _publish_admin_message(message)
        forwarding_engine_instance.schedule(message)
    return JSONResponse(
        status_code=200 if duplicate else 201,
        content={"accepted": True, "duplicate": duplicate, "message_id": str(message.id)},
    )


@router.post("/ingest/mqtt")
async def ingest_mqtt(
    request: Request,
    authorization: str | None = Header(default=None),
    x_mqtt_topic: str | None = Header(default=None),
) -> JSONResponse:
    require_mqtt_bridge(authorization)
    raw_envelope = await read_ingest_body(request)
    try:
        if not x_mqtt_topic:
            raise ValueError("X-MQTT-Topic is required")
        envelope = parse_mqtt_envelope(topic=x_mqtt_topic, raw_envelope=raw_envelope)
        payload = validate_ingest_payload(envelope["payload"])
        message, duplicate = store.ingest(
            raw_body=envelope["raw_body"],
            authorization=None,
            device_id=envelope["device_id"],
            timestamp=envelope["timestamp"],
            nonce=envelope["nonce"],
            signature=envelope["signature"],
            idempotency_key=payload["client_message_id"],
            payload=payload,
            request_hash=hashlib.sha256(envelope["raw_body"]).hexdigest(),
            source_address=_client_ip(request),
            allow_nonce_replay_for_idempotent=True,
        )
    except InvalidDeviceCredential as exc:
        raise RelayError(
            401,
            "invalid_device_token",
            "MQTT device credential is invalid, replayed, or revoked",
        ) from exc
    except IdempotencyConflict as exc:
        raise RelayError(
            409,
            "idempotency_conflict",
            "MQTT idempotency key was already used with a different request body",
        ) from exc
    except (MqttContractError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RelayError(422, "invalid_message", str(exc)) from exc
    if not duplicate:
        otp_event = store.get_otp_delivery(message.id)
        if otp_event:
            await otp_broker.publish(otp_event)
        await _publish_admin_message(message)
        forwarding_engine_instance.schedule(message)
    return JSONResponse(
        status_code=200 if duplicate else 201,
        content={"accepted": True, "duplicate": duplicate, "message_id": str(message.id)},
    )


@router.post("/ingest/messages")
async def ingest(request: Request, authorization: str | None = Header(default=None), x_device_id: str | None = Header(default=None), x_timestamp: str | None = Header(default=None), x_nonce: str | None = Header(default=None), x_signature: str | None = Header(default=None), x_idempotency_key: str | None = Header(default=None)) -> JSONResponse:
    raw = await read_ingest_body(request)
    try:
        payload = validate_ingest_payload(json.loads(raw))
        message, duplicate = store.ingest(
            raw_body=raw,
            authorization=authorization,
            device_id=x_device_id,
            timestamp=x_timestamp,
            nonce=x_nonce,
            signature=x_signature,
            idempotency_key=x_idempotency_key,
            payload=payload,
            request_hash=hashlib.sha256(raw).hexdigest(),
            source_address=_client_ip(request),
        )
    except InvalidDeviceCredential as exc:
        raise RelayError(
            401,
            "invalid_device_token",
            "Device credential is invalid, replayed, or revoked",
        ) from exc
    except IdempotencyConflict as exc:
        raise RelayError(
            409,
            "idempotency_conflict",
            "Idempotency key was already used with a different request body",
        ) from exc
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RelayError(422, "invalid_message", str(exc)) from exc
    if not duplicate:
        otp_event = store.get_otp_delivery(message.id)
        if otp_event:
            await otp_broker.publish(otp_event)
        await _publish_admin_message(message)
        forwarding_engine_instance.schedule(message)
    return JSONResponse(status_code=200 if duplicate else 201, content={"accepted": True, "duplicate": duplicate, "message_id": str(message.id)})


@router.post("/otp/claim")
async def claim_otp(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authenticate_script_token(_script_token_from_authorization(authorization), _client_ip(request))
    raw_phone_number = payload.get("phone_number")
    if not isinstance(raw_phone_number, str) or not raw_phone_number.strip():
        raise RelayError(422, "phone_number_required", "phone_number is required for OTP claim")
    try:
        phone_number = normalize_phone_number(raw_phone_number)
        device_id = UUID(payload["device_id"]) if payload.get("device_id") else None
    except (TypeError, ValueError) as exc:
        raise RelayError(422, "invalid_claim_filter", "phone_number or device_id is invalid") from exc
    try:
        otp = store.claim(
            device_id=device_id,
            phone_number=phone_number,
            sender=payload.get("sender"),
            keyword=payload.get("keyword"),
        )
    except (LookupError, ValueError) as exc:
        raise RelayError(404, "otp_not_available", "No available OTP matched the filters") from exc
    message = store.get_message(otp.message_id)
    if not message:
        raise RelayError(404, "message_not_found", "Source message no longer exists")
    return {
        "otp_id": str(otp.id),
        "code": otp.code,
        "sender": message.sender,
        "message_id": str(message.id),
        "created_at": message.created_at.isoformat(),
        "expires_at": otp.expires_at.isoformat(),
    }


@router.get("/otp/claim.txt", response_class=PlainTextResponse)
async def claim_otp_text(
    request: Request,
    phone_number: str | None = None,
    sender: str | None = None,
    keyword: str | None = None,
    device_id: UUID | None = None,
    authorization: str | None = Header(default=None),
) -> PlainTextResponse:
    _authenticate_script_token(_script_token_from_authorization(authorization), _client_ip(request))
    if not isinstance(phone_number, str) or not phone_number.strip():
        raise RelayError(422, "phone_number_required", "phone_number is required for OTP claim")
    try:
        phone_number = normalize_phone_number(phone_number)
    except ValueError as exc:
        raise RelayError(422, "invalid_claim_filter", "phone_number is invalid") from exc
    try:
        otp = store.claim(
            device_id=device_id,
            phone_number=phone_number,
            sender=sender,
            keyword=keyword,
        )
    except (LookupError, ValueError) as exc:
        raise RelayError(404, "otp_not_available", "No available OTP matched the filters") from exc
    return PlainTextResponse(otp.code)


@router.get("/admin/messages")
async def list_messages(
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
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    x_admin_token: str | None = Header(default=None),
) -> list[dict[str, Any]]:
    require_admin(x_admin_token)
    if identity_status not in {None, "resolved", "unresolved", "conflict"}:
        raise RelayError(422, "invalid_filter", "Unsupported receiving identity status")
    if otp_status not in {None, "available", "claimed", "expired"}:
        raise RelayError(422, "invalid_filter", "Unsupported OTP status")
    if (created_after is None) != (after_id is None):
        raise RelayError(
            422,
            "invalid_filter",
            "created_after and after_id must be provided together",
        )
    if created_after is not None and (
        created_after.tzinfo is None or created_after.utcoffset() is None
    ):
        raise RelayError(422, "invalid_filter", "created_after must include a timezone")
    try:
        messages = store.list_messages(
            phone_number=phone_number,
            device_id=device_id,
            sim_slot=sim_slot,
            sender=sender,
            keyword=keyword,
            identity_status=identity_status,
            otp_status=otp_status,
            read=read,
            created_after=created_after,
            after_id=after_id,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise RelayError(422, "invalid_filter", str(exc)) from exc
    states = store.get_otp_states([message.id for message in messages])
    return [
        _serialize_message(message, otp_code=(states.get(str(message.id)) or {}).get("code"))
        for message in messages
    ]


@router.post("/admin/messages/{message_id}/read")
async def mark_message_read(
    message_id: UUID,
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin(x_admin_token)
    try:
        return _serialize_message_with_otp(store.set_message_read(message_id, read=True))
    except KeyError as exc:
        raise RelayError(404, "message_not_found", "Message not found") from exc


@router.post("/admin/messages/{message_id}/unread")
async def mark_message_unread(
    message_id: UUID,
    x_admin_token: str | None = Header(default=None),
) -> dict[str, Any]:
    require_admin(x_admin_token)
    try:
        return _serialize_message_with_otp(store.set_message_read(message_id, read=False))
    except KeyError as exc:
        raise RelayError(404, "message_not_found", "Message not found") from exc


@router.delete("/admin/messages/{message_id}", status_code=204)
async def delete_message(
    message_id: UUID,
    x_admin_token: str | None = Header(default=None),
) -> None:
    require_admin(x_admin_token)
    try:
        store.delete_message(message_id)
    except KeyError as exc:
        raise RelayError(404, "message_not_found", "Message not found") from exc


@router.post("/admin/messages/bulk-delete")
async def bulk_delete_messages(
    payload: Any = Body(...),
    x_admin_token: str | None = Header(default=None),
) -> dict[str, int]:
    require_admin(x_admin_token)
    if not isinstance(payload, dict) or not isinstance(payload.get("message_ids"), list):
        raise RelayError(422, "invalid_message_ids", "message_ids must be an array")
    raw_ids = payload["message_ids"]
    if not raw_ids or len(raw_ids) > 500 or any(not isinstance(value, str) for value in raw_ids):
        raise RelayError(422, "invalid_message_ids", "message_ids must contain 1 to 500 UUIDs")
    try:
        message_ids = [UUID(value) for value in raw_ids]
    except ValueError as exc:
        raise RelayError(422, "invalid_message_ids", "message_ids must contain valid UUIDs") from exc
    return {"deleted": store.delete_messages(message_ids)}


@router.get("/admin/otps")
async def list_otps(x_admin_token: str | None = Header(default=None)) -> list[dict[str, Any]]:
    require_admin(x_admin_token)
    return [{"id": str(o.id), "message_id": str(o.message_id), "status": o.status, "expires_at": o.expires_at.isoformat()} for o in store.list_otps()]


@router.post("/admin/retention/run")
async def run_retention(x_admin_token: str | None = Header(default=None)) -> dict[str, int]:
    require_admin(x_admin_token)
    return {"redacted_messages": store.run_retention(settings.retention_days)}


@router.post("/admin/api-clients", status_code=201)
async def create_api_client(x_admin_token: str | None = Header(default=None)) -> dict[str, str]:
    require_admin(x_admin_token)
    return {"token": store.create_api_client()}


@router.get("/admin/api-clients")
async def list_api_clients(
    x_admin_token: str | None = Header(default=None),
) -> dict[str, list[dict[str, object]]]:
    require_admin(x_admin_token)
    return {"clients": store.list_api_clients()}
