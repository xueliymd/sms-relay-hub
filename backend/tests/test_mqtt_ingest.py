import base64
import json
from datetime import datetime, timezone
from typing import Any, cast

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.core.config import settings
from app.services.mqtt_contract import build_mqtt_envelope, mqtt_message_topic

ADMIN_HEADERS = {"X-Admin-Token": "neutral-admin-token-for-tests-123456"}


def create_mqtt_device(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/v1/admin/devices",
        json={"name": "Air724 MQTT test", "client_type": "air724_mqtt", "auth_kind": "hmac"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


def test_mqtt_envelope_round_trip_uses_existing_hmac_contract() -> None:
    secret = base64.urlsafe_b64encode(b"m" * 32).decode().rstrip("=")
    message = {
        "schema_version": 1,
        "client_message_id": "air724-mqtt-1",
        "received_at": datetime(2026, 9, 2, 1, 2, 3, tzinfo=timezone.utc).isoformat(),
        "sender": "10690000",
        "body": "验证码 482731",
        "source": "air724_mqtt",
        "metadata": {"imei": "868600000000001", "collector_version": "mqtt-1"},
        "sim_slot": 1,
    }

    envelope = build_mqtt_envelope(
        device_id="device-1",
        hmac_secret=secret,
        message=message,
        timestamp="1788310923",
        nonce="nonce-1",
    )

    assert envelope["device_id"] == "device-1"
    assert envelope["payload_b64"]
    assert mqtt_message_topic("device-1") == "relay/v1/devices/device-1/messages"


def test_mqtt_ingest_accepts_signed_envelope_and_is_idempotent(
    client: TestClient, monkeypatch: MonkeyPatch
) -> None:
    created = create_mqtt_device(client)
    device_id = created["device"]["id"]
    secret = created["credential"]["secret"]
    bridge_token = "mqtt-bridge-token-for-tests-1234567890"
    monkeypatch.setattr(settings, "mqtt_bridge_token", bridge_token)
    message = {
        "schema_version": 1,
        "client_message_id": "air724-mqtt-idempotent-1",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "sender": "10690000",
        "body": "验证码 482731",
        "source": "air724_mqtt",
        "metadata": {"imei": "868600000000001"},
        "sim_slot": 1,
    }
    envelope = build_mqtt_envelope(
        device_id=device_id,
        hmac_secret=secret,
        message=message,
        timestamp=str(int(datetime.now(timezone.utc).timestamp())),
        nonce="nonce-idempotent-1",
    )
    retry_envelope = build_mqtt_envelope(
        device_id=device_id,
        hmac_secret=secret,
        message=message,
        timestamp=envelope["timestamp"],
        nonce="nonce-idempotent-retry-1",
    )
    raw = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()
    retry_raw = json.dumps(retry_envelope, ensure_ascii=False, separators=(",", ":")).encode()
    headers = {
        "Authorization": f"Bearer {bridge_token}",
        "X-MQTT-Topic": mqtt_message_topic(device_id),
        "Content-Type": "application/json",
    }

    first = client.post("/api/v1/ingest/mqtt", content=raw, headers=headers)
    second = client.post("/api/v1/ingest/mqtt", content=retry_raw, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["duplicate"] is True


def test_mqtt_ingest_accepts_exact_qos1_redelivery(
    client: TestClient, monkeypatch: MonkeyPatch
) -> None:
    created = create_mqtt_device(client)
    device_id = created["device"]["id"]
    bridge_token = "mqtt-bridge-token-for-tests-1234567890"
    monkeypatch.setattr(settings, "mqtt_bridge_token", bridge_token)
    message = {
        "schema_version": 1,
        "client_message_id": "air724-mqtt-qos1-redelivery-1",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "sender": "10690000",
        "body": "验证码 193746",
        "source": "air724_mqtt",
        "metadata": {"imei": "868600000000001"},
    }
    envelope = build_mqtt_envelope(
        device_id=device_id,
        hmac_secret=created["credential"]["secret"],
        message=message,
        timestamp=str(int(datetime.now(timezone.utc).timestamp())),
        nonce="nonce-qos1-redelivery-1",
    )
    raw = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()
    headers = {
        "Authorization": f"Bearer {bridge_token}",
        "X-MQTT-Topic": mqtt_message_topic(device_id),
        "Content-Type": "application/json",
    }

    first = client.post("/api/v1/ingest/mqtt", content=raw, headers=headers)
    redelivery = client.post("/api/v1/ingest/mqtt", content=raw, headers=headers)

    assert first.status_code == 201
    assert redelivery.status_code == 200
    assert redelivery.json()["duplicate"] is True


def test_mqtt_ingest_accepts_root_module_source(
    client: TestClient, monkeypatch: MonkeyPatch
) -> None:
    created = client.post(
        "/api/v1/admin/devices",
        json={"name": "Root MQTT test", "client_type": "root_module", "auth_kind": "hmac"},
        headers=ADMIN_HEADERS,
    )
    assert created.status_code == 201
    device = created.json()
    bridge_token = "mqtt-bridge-token-for-tests-1234567890"
    monkeypatch.setattr(settings, "mqtt_bridge_token", bridge_token)
    message = {
        "schema_version": 1,
        "client_message_id": "root-mqtt-1",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "sender": "10690000",
        "body": "验证码 728194",
        "source": "root_module",
        "metadata": {"collector": "root-module"},
    }
    envelope = build_mqtt_envelope(
        device_id=device["device"]["id"],
        hmac_secret=device["credential"]["secret"],
        message=message,
        timestamp=str(int(datetime.now(timezone.utc).timestamp())),
        nonce="root-mqtt-nonce-1",
        source="root_module",
    )
    headers = {
        "Authorization": f"Bearer {bridge_token}",
        "X-MQTT-Topic": mqtt_message_topic(device["device"]["id"]),
        "Content-Type": "application/json",
    }
    response = client.post(
        "/api/v1/ingest/mqtt",
        content=json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode(),
        headers=headers,
    )
    assert response.status_code == 201
