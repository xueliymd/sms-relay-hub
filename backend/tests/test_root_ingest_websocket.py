import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, cast

from fastapi.testclient import TestClient

ADMIN_HEADERS = {"X-Admin-Token": "neutral-admin-token-for-tests-123456"}


def _signed_frame(device: dict[str, Any], *, nonce: str, message_id: str) -> dict[str, str]:
    message = {
        "schema_version": 1,
        "client_message_id": message_id,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "sender": "10690000",
        "body": "验证码 482731",
        "source": "root_module",
        "metadata": {"collector": "root-module"},
    }
    raw_body = json.dumps(message, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    digest = hashlib.sha256(raw_body).hexdigest()
    canonical = f"{timestamp}\n{nonce}\n{digest}".encode()
    secret = base64.urlsafe_b64decode(device["credential"]["secret"] + "=" * (-len(device["credential"]["secret"]) % 4))
    signature = base64.urlsafe_b64encode(hmac.new(secret, canonical, hashlib.sha256).digest()).decode().rstrip("=")
    return {
        "type": "message",
        "device_id": device["device"]["id"],
        "timestamp": timestamp,
        "nonce": nonce,
        "signature": signature,
        "idempotency_key": message_id,
        "payload_b64": base64.urlsafe_b64encode(raw_body).decode().rstrip("="),
    }


def _create_root_device(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/v1/admin/devices",
        json={"name": "Root WS test", "client_type": "root_module", "auth_kind": "hmac"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


def test_root_ingest_websocket_accepts_and_deduplicates(client: TestClient) -> None:
    device = _create_root_device(client)
    frame = _signed_frame(device, nonce="root-ws-nonce-1", message_id="root-ws-1")

    with client.websocket_connect("/api/v1/ws/ingest") as websocket:
        assert websocket.receive_json() == {"type": "ready"}
        websocket.send_json(frame)
        first = websocket.receive_json()
        assert first["accepted"] is True
        assert first["duplicate"] is False
        websocket.send_json(frame)
        second = websocket.receive_json()
        assert second["accepted"] is True
        assert second["duplicate"] is True
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json() == {"type": "pong"}

