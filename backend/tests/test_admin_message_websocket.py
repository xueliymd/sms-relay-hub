import asyncio
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from app.services.admin_message_websocket import (
    AdminMessageSubscription,
    AdminMessageWebSocketBroker,
)


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent.append(payload)


ADMIN_TOKEN = "neutral-admin-token-for-tests-123456"
ADMIN_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}


def create_ingest_device(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/v1/admin/devices",
        json={
            "name": "Admin WebSocket test device",
            "client_type": "ios_shortcut",
            "auth_kind": "bearer",
        },
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


def ingest_message(
    client: TestClient,
    created: dict[str, object],
    client_message_id: str,
    *,
    sim_slot: int = 1,
) -> object:
    credential = created["credential"]
    assert isinstance(credential, dict)
    token = credential["secret"]
    response = client.post(
        "/api/v1/ingest/messages",
        json={
            "schema_version": 1,
            "client_message_id": client_message_id,
            "received_at": "2026-09-01T01:00:00+00:00",
            "sender": "Relay Service",
            "body": "Your verification code is 482731",
            "source": "ios_shortcut",
            "sim_slot": sim_slot,
            "metadata": {"client_version": "1.0.11"},
        },
        headers={
            "Authorization": f"Bearer {token}",
            "X-Idempotency-Key": client_message_id,
        },
    )
    return response


def test_admin_message_websocket_pushes_full_committed_message(client: TestClient) -> None:
    created = create_ingest_device(client)
    device = created["device"]
    assert isinstance(device, dict)
    device_id = device["id"]

    with client.websocket_connect("/api/v1/ws/admin/messages") as websocket:
        websocket.send_json({"type": "auth", "token": ADMIN_TOKEN})
        assert websocket.receive_json() == {"type": "auth_ok"}
        websocket.send_json({"type": "subscribe", "device_id": device_id, "sim_slot": 1})
        subscribed = websocket.receive_json()
        assert subscribed["type"] == "subscribed"
        assert subscribed["device_id"] == device_id
        assert subscribed["sim_slot"] == 1

        response = ingest_message(client, created, "admin-ws-full-message")
        assert getattr(response, "status_code") == 201
        event = websocket.receive_json()

    assert event["type"] == "message"
    assert event["device_id"] == device_id
    assert event["sim_slot"] == 1
    assert event["sender"] == "Relay Service"
    assert event["body"] == "Your verification code is 482731"
    assert event["otp_code"] == "482731"
    assert event["created_at"]
    assert event["cursor"] == {"created_at": event["created_at"], "id": event["id"]}


def test_rest_backfill_matches_websocket_payload(client: TestClient) -> None:
    """网页端断线重连会用 REST 补拉结果按 id 覆盖 WS 同一条消息（LiveMessagePanel）。
    REST 若少了 otp_code，"验证码"按钮会在一次重连后消失——这里锁死两条路径字段一致。"""
    created = create_ingest_device(client)
    device = created["device"]
    assert isinstance(device, dict)
    device_id = device["id"]

    with client.websocket_connect("/api/v1/ws/admin/messages") as websocket:
        websocket.send_json({"type": "auth", "token": ADMIN_TOKEN})
        assert websocket.receive_json() == {"type": "auth_ok"}
        websocket.send_json({"type": "subscribe", "device_id": device_id, "sim_slot": 1})
        assert websocket.receive_json()["type"] == "subscribed"
        assert getattr(ingest_message(client, created, "admin-ws-rest-parity"), "status_code") == 201
        event = websocket.receive_json()

    listed = client.get(
        "/api/v1/admin/messages",
        params={"device_id": device_id},
        headers=ADMIN_HEADERS,
    )
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    backfill = rows[0]

    assert event["otp_code"] == backfill["otp_code"] == "482731"
    for field in (
        "id",
        "device_id",
        "client_message_id",
        "sender",
        "body",
        "source",
        "sim_slot",
        "otp_status",
        "created_at",
        "received_at",
    ):
        assert event[field] == backfill[field], field


def test_admin_message_websocket_can_subscribe_to_all_active_devices(
    client: TestClient,
) -> None:
    first = create_ingest_device(client)
    second = create_ingest_device(client)
    first_device = first["device"]
    second_device = second["device"]
    assert isinstance(first_device, dict)
    assert isinstance(second_device, dict)

    with client.websocket_connect("/api/v1/ws/admin/messages") as websocket:
        websocket.send_json({"type": "auth", "token": ADMIN_TOKEN})
        assert websocket.receive_json() == {"type": "auth_ok"}
        websocket.send_json({"type": "subscribe"})
        subscribed = websocket.receive_json()
        assert subscribed["type"] == "subscribed"
        assert subscribed["device_id"] is None

        assert getattr(ingest_message(client, first, "admin-ws-all-first"), "status_code") == 201
        first_event = websocket.receive_json()
        assert getattr(ingest_message(client, second, "admin-ws-all-second"), "status_code") == 201
        second_event = websocket.receive_json()

    assert {first_event["device_id"], second_event["device_id"]} == {
        first_device["id"],
        second_device["id"],
    }


def test_admin_message_websocket_rejects_invalid_token_and_inactive_device(
    client: TestClient,
) -> None:
    with client.websocket_connect("/api/v1/ws/admin/messages") as websocket:
        websocket.send_json({"type": "auth", "token": "invalid-admin-token"})
        assert websocket.receive_json()["code"] == "admin_auth_required"

    created = create_ingest_device(client)
    device = created["device"]
    assert isinstance(device, dict)
    device_id = device["id"]
    assert client.delete(
        f"/api/v1/admin/devices/{device_id}", headers=ADMIN_HEADERS
    ).status_code == 204

    with client.websocket_connect("/api/v1/ws/admin/messages") as websocket:
        websocket.send_json({"type": "auth", "token": ADMIN_TOKEN})
        assert websocket.receive_json() == {"type": "auth_ok"}
        websocket.send_json({"type": "subscribe", "device_id": device_id})
        assert websocket.receive_json()["code"] == "device_not_active"


def test_admin_message_websocket_suppresses_nonmatching_and_duplicate_ingest(
    client: TestClient,
) -> None:
    selected = create_ingest_device(client)
    other = create_ingest_device(client)
    selected_device = selected["device"]
    assert isinstance(selected_device, dict)

    with client.websocket_connect("/api/v1/ws/admin/messages") as websocket:
        websocket.send_json({"type": "auth", "token": ADMIN_TOKEN})
        assert websocket.receive_json() == {"type": "auth_ok"}
        websocket.send_json({"type": "subscribe", "device_id": selected_device["id"]})
        assert websocket.receive_json()["type"] == "subscribed"

        assert getattr(ingest_message(client, other, "admin-ws-other"), "status_code") == 201
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json() == {"type": "pong"}

        first = ingest_message(client, selected, "admin-ws-duplicate")
        assert getattr(first, "status_code") == 201
        assert websocket.receive_json()["type"] == "message"
        duplicate = ingest_message(client, selected, "admin-ws-duplicate")
        assert getattr(duplicate, "status_code") == 200
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json() == {"type": "pong"}


def test_admin_message_websocket_revalidates_login_session_on_ping(
    client: TestClient,
) -> None:
    created = create_ingest_device(client)
    device = created["device"]
    assert isinstance(device, dict)
    login = client.post(
        "/api/v1/admin/login",
        json={
            "username": "admin",
            "password": "neutral-admin-password-for-tests-123456",
        },
    )
    assert login.status_code == 200
    session_token = login.json()["token"]

    with client.websocket_connect("/api/v1/ws/admin/messages") as websocket:
        websocket.send_json({"type": "auth", "token": session_token})
        assert websocket.receive_json() == {"type": "auth_ok"}
        websocket.send_json({"type": "subscribe", "device_id": device["id"]})
        assert websocket.receive_json()["type"] == "subscribed"

        updated = client.put(
            "/api/v1/admin/account",
            headers={"X-Admin-Token": session_token},
            json={
                "current_password": "neutral-admin-password-for-tests-123456",
                "password": "new-neutral-admin-password-123456",
            },
        )
        assert updated.status_code == 200
        websocket.send_json({"type": "ping"})
        error = websocket.receive_json()

    assert error["code"] == "admin_auth_required"


def test_subscription_matches_device_and_optional_sim_slot() -> None:
    subscription = AdminMessageSubscription(device_id="device-1", sim_slot=1)

    assert subscription.matches({"device_id": "device-1", "sim_slot": 1})
    assert not subscription.matches({"device_id": "device-2", "sim_slot": 1})
    assert not subscription.matches({"device_id": "device-1", "sim_slot": 0})


def test_device_only_subscription_accepts_every_sim_slot() -> None:
    subscription = AdminMessageSubscription(device_id=" device-1 ")

    assert subscription.device_id == "device-1"
    assert subscription.matches({"device_id": "device-1", "sim_slot": 0})
    assert subscription.matches({"device_id": "device-1", "sim_slot": 1})
    assert not subscription.matches({"device_id": "device-2", "sim_slot": 1})


@pytest.mark.parametrize(
    ("device_id", "sim_slot"),
    [("", None), ("   ", None), ("device-1", -1), ("device-1", True)],
)
def test_subscription_rejects_invalid_filters(device_id: str, sim_slot: int | None) -> None:
    with pytest.raises(ValueError):
        AdminMessageSubscription(device_id=device_id, sim_slot=sim_slot)


def test_duplicate_add_replaces_subscription_and_remove_is_idempotent() -> None:
    asyncio.run(_test_duplicate_add_replaces_subscription_and_remove_is_idempotent())


async def _test_duplicate_add_replaces_subscription_and_remove_is_idempotent() -> None:
    broker = AdminMessageWebSocketBroker()
    socket = FakeWebSocket()
    await broker.add(socket, AdminMessageSubscription(device_id="device-1"))
    await broker.add(socket, AdminMessageSubscription(device_id="device-2", sim_slot=1))

    assert await broker.count() == 1
    assert await broker.publish({"device_id": "device-1", "sim_slot": 0}) == 0
    assert await broker.publish({"device_id": "device-2", "sim_slot": 1}) == 1
    assert socket.sent == [{"device_id": "device-2", "sim_slot": 1}]

    await broker.remove(socket)
    await broker.remove(socket)
    assert await broker.count() == 0


def test_failing_socket_does_not_block_other_subscribers() -> None:
    asyncio.run(_test_failing_socket_does_not_block_other_subscribers())


async def _test_failing_socket_does_not_block_other_subscribers() -> None:
    class BrokenSocket(FakeWebSocket):
        async def send_json(self, payload: dict[str, object]) -> None:
            raise RuntimeError("closed")

    broker = AdminMessageWebSocketBroker()
    broken = BrokenSocket()
    healthy = FakeWebSocket()
    subscription = AdminMessageSubscription(device_id="device-1")
    await broker.add(broken, subscription)
    await broker.add(healthy, subscription)

    delivered = await broker.publish({"type": "message", "device_id": "device-1"})

    assert delivered == 1
    assert healthy.sent == [{"type": "message", "device_id": "device-1"}]
    assert await broker.count() == 1


def test_send_timeout_removes_slow_socket_without_blocking_fast_socket() -> None:
    asyncio.run(_test_send_timeout_removes_slow_socket_without_blocking_fast_socket())


async def _test_send_timeout_removes_slow_socket_without_blocking_fast_socket() -> None:
    class SlowSocket(FakeWebSocket):
        async def send_json(self, payload: dict[str, object]) -> None:
            await asyncio.sleep(1)

    broker = AdminMessageWebSocketBroker(send_timeout_seconds=0.01)
    slow = SlowSocket()
    fast = FakeWebSocket()
    subscription = AdminMessageSubscription(device_id="device-1")
    await broker.add(slow, subscription)
    await broker.add(fast, subscription)

    delivered = await broker.publish({"type": "message", "device_id": "device-1"})

    assert delivered == 1
    assert fast.sent == [{"type": "message", "device_id": "device-1"}]
    assert await broker.count() == 1
