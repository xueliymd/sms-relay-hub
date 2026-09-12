from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from typing import Any


class MqttContractError(ValueError):
    pass


_TOPIC_PATTERN = re.compile(r"^relay/v1/devices/([^/]+)/messages$")
_SUPPORTED_PAYLOAD_SOURCES = {"air724_mqtt", "root_module"}


def mqtt_message_topic(device_id: str) -> str:
    normalized = device_id.strip()
    if not normalized or len(normalized) > 160 or "/" in normalized:
        raise MqttContractError("device_id is not valid for an MQTT topic")
    return f"relay/v1/devices/{normalized}/messages"


def _decode_secret(value: str) -> bytes:
    normalized = value.strip()
    if not normalized:
        raise MqttContractError("HMAC secret is empty")
    try:
        return base64.urlsafe_b64decode(normalized + "=" * (-len(normalized) % 4))
    except (binascii.Error, ValueError) as error:
        raise MqttContractError("HMAC secret is not valid base64url") from error


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _signature(*, hmac_secret: str, timestamp: str, nonce: str, raw_body: bytes) -> str:
    digest = hashlib.sha256(raw_body).hexdigest()
    canonical = f"{timestamp}\n{nonce}\n{digest}".encode()
    value = hmac.new(_decode_secret(hmac_secret), canonical, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def build_mqtt_envelope(
    *,
    device_id: str,
    hmac_secret: str,
    message: dict[str, Any],
    timestamp: str,
    nonce: str,
    source: str = "air724_mqtt",
) -> dict[str, str]:
    if message.get("schema_version") != 1 or message.get("source") != source or source not in _SUPPORTED_PAYLOAD_SOURCES:
        raise MqttContractError("MQTT message source is not supported")
    raw_body = _canonical_json(message)
    return {
        "schema_version": "1",
        "device_id": device_id,
        "timestamp": timestamp,
        "nonce": nonce,
        "signature": _signature(
            hmac_secret=hmac_secret,
            timestamp=timestamp,
            nonce=nonce,
            raw_body=raw_body,
        ),
        "payload_b64": base64.urlsafe_b64encode(raw_body).decode("ascii").rstrip("="),
    }


def parse_mqtt_envelope(
    *,
    topic: str,
    raw_envelope: bytes,
) -> dict[str, Any]:
    match = _TOPIC_PATTERN.fullmatch(topic.strip())
    if match is None:
        raise MqttContractError("MQTT topic is not supported")
    try:
        envelope = json.loads(raw_envelope.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MqttContractError("MQTT envelope must be valid JSON") from error
    if not isinstance(envelope, dict) or envelope.get("schema_version") != "1":
        raise MqttContractError("MQTT envelope schema_version must be 1")
    device_id = envelope.get("device_id")
    timestamp = envelope.get("timestamp")
    nonce = envelope.get("nonce")
    signature = envelope.get("signature")
    payload_b64 = envelope.get("payload_b64")
    if not all(isinstance(value, str) and value.strip() for value in (device_id, timestamp, nonce, signature, payload_b64)):
        raise MqttContractError("MQTT envelope authentication fields are required")
    payload_b64_text = payload_b64 if isinstance(payload_b64, str) else ""
    if device_id != match.group(1):
        raise MqttContractError("MQTT topic and envelope device_id do not match")
    try:
        raw_body = base64.urlsafe_b64decode(payload_b64_text + "=" * (-len(payload_b64_text) % 4))
        payload = json.loads(raw_body.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MqttContractError("MQTT payload_b64 is invalid") from error
    if not isinstance(payload, dict):
        raise MqttContractError("MQTT payload must be a JSON object")
    if payload.get("schema_version") != 1 or payload.get("source") not in _SUPPORTED_PAYLOAD_SOURCES:
        raise MqttContractError("MQTT payload source is not supported")
    return {
        "device_id": device_id,
        "timestamp": timestamp,
        "nonce": nonce,
        "signature": signature,
        "raw_body": raw_body,
        "payload": payload,
    }


def verify_mqtt_signature(*, hmac_secret: str, envelope: dict[str, Any]) -> bool:
    expected = _signature(
        hmac_secret=hmac_secret,
        timestamp=str(envelope["timestamp"]),
        nonce=str(envelope["nonce"]),
        raw_body=bytes(envelope["raw_body"]),
    )
    return hmac.compare_digest(expected, str(envelope["signature"]))
