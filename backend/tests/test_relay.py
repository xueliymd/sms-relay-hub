import base64
import hashlib
import hmac
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID

import pytest
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, update

from app.api.relay import build_shortcut_payload
from app.core.crypto import EncryptedValue
from app.db.models import (
    ApiClientModel,
    AuditEventModel,
    DeviceModel,
    DeviceNonceModel,
    DeviceSimProfileModel,
    MessageModel,
    OtpModel,
)
from app.services.store import RelayStore, normalize_phone_number, store

ADMIN_HEADERS = {"X-Admin-Token": "neutral-admin-token-for-tests-123456"}


def test_normalize_phone_number_uses_one_mainland_e164_form() -> None:
    assert normalize_phone_number("13800138000") == "+8613800138000"
    assert normalize_phone_number("8613800138000") == "+8613800138000"
    assert normalize_phone_number("+8613800138000") == "+8613800138000"


def relay_payload(
    client_id: str = "relay-event-1",
    body: str = "Your one-time access code is 482731",
    source: str = "ios_shortcut",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "client_message_id": client_id,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "sender": "Service Relay",
        "body": body,
        "source": source,
        "metadata": {"client_version": "1.0.0"},
    }


def create_device(client: TestClient, *, client_type: str, auth_kind: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/admin/devices",
        json={"name": "Authorized test device", "client_type": client_type, "auth_kind": auth_kind},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


def create_claim_client(client: TestClient) -> str:
    response = client.post("/api/v1/admin/api-clients", headers=ADMIN_HEADERS)
    assert response.status_code == 201
    return cast(str, response.json()["token"])


def test_incremental_admin_messages_use_a_stable_device_and_sim_cursor(
    client: TestClient,
) -> None:
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    device_id = created["device"]["id"]
    token = created["credential"]["secret"]

    def ingest_message(client_message_id: str, sim_slot: int) -> str:
        payload = relay_payload(client_message_id, body=f"Message {client_message_id}")
        payload["sim_slot"] = sim_slot
        response = client.post(
            "/api/v1/ingest/messages",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "X-Idempotency-Key": client_message_id,
            },
        )
        assert response.status_code == 201
        return cast(str, response.json()["message_id"])

    first_id = ingest_message("incremental-first", 0)
    second_id = ingest_message("incremental-second", 1)
    third_id = ingest_message("incremental-third", 1)

    other = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    other_payload = relay_payload("incremental-other", body="Other device message")
    other_payload["sim_slot"] = 1
    other_response = client.post(
        "/api/v1/ingest/messages",
        json=other_payload,
        headers={
            "Authorization": f"Bearer {other['credential']['secret']}",
            "X-Idempotency-Key": "incremental-other",
        },
    )
    assert other_response.status_code == 201

    first_created_at = datetime(2026, 9, 1, 1, 0, tzinfo=timezone.utc)
    next_created_at = first_created_at + timedelta(seconds=1)
    with store.session_factory.begin() as session:
        session.execute(
            update(MessageModel)
            .where(MessageModel.id == first_id)
            .values(created_at=first_created_at)
        )
        session.execute(
            update(MessageModel)
            .where(MessageModel.id.in_([second_id, third_id]))
            .values(created_at=next_created_at)
        )

    response = client.get(
        "/api/v1/admin/messages",
        params={
            "device_id": device_id,
            "sim_slot": 1,
            "created_after": first_created_at.isoformat(),
            "after_id": first_id,
        },
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    expected_same_timestamp_ids = sorted([second_id, third_id])
    assert [item["id"] for item in response.json()] == expected_same_timestamp_ids
    assert {item["device_id"] for item in response.json()} == {device_id}
    assert {item["sim_slot"] for item in response.json()} == {1}

    same_timestamp_response = client.get(
        "/api/v1/admin/messages",
        params={
            "device_id": device_id,
            "created_after": next_created_at.isoformat(),
            "after_id": expected_same_timestamp_ids[0],
        },
        headers=ADMIN_HEADERS,
    )
    assert same_timestamp_response.status_code == 200
    assert [item["id"] for item in same_timestamp_response.json()] == [
        expected_same_timestamp_ids[1]
    ]


def test_incremental_admin_messages_reject_invalid_cursor_and_oversized_limit(
    client: TestClient,
) -> None:
    naive_cursor = client.get(
        "/api/v1/admin/messages",
        params={
            "created_after": "2026-09-01T01:00:00",
            "after_id": "00000000-0000-0000-0000-000000000001",
        },
        headers=ADMIN_HEADERS,
    )
    assert naive_cursor.status_code == 422

    oversized = client.get(
        "/api/v1/admin/messages",
        params={"limit": 501},
        headers=ADMIN_HEADERS,
    )
    assert oversized.status_code == 422


def test_script_api_tokens_are_listed_for_admin_after_creation(client: TestClient) -> None:
    created = client.post("/api/v1/admin/api-clients", headers=ADMIN_HEADERS)
    assert created.status_code == 201
    token = created.json()["token"]

    listed = client.get("/api/v1/admin/api-clients", headers=ADMIN_HEADERS)

    assert listed.status_code == 200
    assert listed.json()["clients"]
    assert listed.json()["clients"][0]["token"] == token
    assert listed.json()["clients"][0]["active"] is True


def test_air724_enrollment_creates_a_script_token_without_ingest_access(
    client: TestClient,
) -> None:
    created = create_device(client, client_type="air724", auth_kind="script_api")

    assert created["device"]["client_type"] == "air724"
    assert created["credential"]["type"] == "script_api"
    token = created["credential"]["secret"]
    assert token.startswith("srh_api_")

    listed_clients = client.get("/api/v1/admin/api-clients", headers=ADMIN_HEADERS)
    assert listed_clients.status_code == 200
    assert all(item["token"] != token for item in listed_clients.json()["clients"])

    denied = client.post(
        "/api/v1/ingest/messages",
        json=relay_payload("air724-must-not-ingest", source="air724"),
        headers={"Authorization": f"Bearer {token}", "X-Idempotency-Key": "air724-must-not-ingest"},
    )
    assert denied.status_code == 401

    assert store.authenticate_api_client(token) is True
    device = store.get_device(UUID(created["device"]["id"]))
    assert device is not None
    assert device.last_seen_at is not None


def test_air724_lifecycle_rotates_revokes_restores_and_purges_linked_token(
    client: TestClient,
) -> None:
    created = create_device(client, client_type="air724", auth_kind="script_api")
    device_id = created["device"]["id"]
    old_token = created["credential"]["secret"]

    rotated = client.post(
        f"/api/v1/admin/devices/{device_id}/rotate-credential", headers=ADMIN_HEADERS
    )
    assert rotated.status_code == 200
    new_token = rotated.json()["credential"]["secret"]
    assert rotated.json()["credential"]["type"] == "script_api"
    assert new_token != old_token
    assert store.authenticate_api_client(old_token) is False
    assert store.authenticate_api_client(new_token) is True

    assert client.delete(f"/api/v1/admin/devices/{device_id}", headers=ADMIN_HEADERS).status_code == 204
    assert store.authenticate_api_client(new_token) is False

    restored = client.post(
        f"/api/v1/admin/devices/{device_id}/restore", headers=ADMIN_HEADERS
    )
    assert restored.status_code == 200
    restored_token = restored.json()["credential"]["secret"]
    assert restored.json()["credential"]["type"] == "script_api"
    assert restored_token not in {old_token, new_token}
    assert store.authenticate_api_client(restored_token) is True

    assert client.delete(f"/api/v1/admin/devices/{device_id}", headers=ADMIN_HEADERS).status_code == 204
    assert client.delete(
        f"/api/v1/admin/devices/{device_id}/purge", headers=ADMIN_HEADERS
    ).status_code == 204
    assert store.authenticate_api_client(restored_token) is False
    with store.session_factory() as session:
        assert session.scalar(
            select(ApiClientModel).where(ApiClientModel.device_id == device_id)
        ) is None


def hmac_request(
    device_id: str,
    secret: str,
    data: dict[str, Any],
    *,
    nonce: str,
) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(data, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    key = base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))
    canonical = f"{timestamp}\n{nonce}\n{hashlib.sha256(raw).hexdigest()}".encode()
    signature = (
        base64.urlsafe_b64encode(hmac.new(key, canonical, hashlib.sha256).digest())
        .decode()
        .rstrip("=")
    )
    return raw, {
        "Content-Type": "application/json",
        "X-Device-Id": device_id,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
        "X-Idempotency-Key": str(data["client_message_id"]),
    }


def test_bearer_ingest_is_idempotent_and_claimable(client: TestClient) -> None:
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    token = created["credential"]["secret"]
    data = relay_payload()
    data["metadata"]["phone_number"] = "+15550100005"
    headers = {"Authorization": f"Bearer {token}", "X-Idempotency-Key": "relay-event-1"}

    first = client.post("/api/v1/ingest/messages", json=data, headers=headers)
    second = client.post("/api/v1/ingest/messages", json=data, headers=headers)
    conflict = client.post(
        "/api/v1/ingest/messages",
        json=relay_payload(body="Your one-time access code is 927415"),
        headers=headers,
    )

    assert first.status_code == 201
    assert second.status_code == 200 and second.json()["duplicate"] is True
    assert first.json()["message_id"] == second.json()["message_id"]
    assert conflict.status_code == 409

    api_token = create_claim_client(client)
    claim_headers = {"Authorization": f"Bearer {api_token}"}
    claimed = client.post(
        "/api/v1/otp/claim",
        json={"phone_number": "+15550100005"},
        headers=claim_headers,
    )
    assert claimed.status_code == 200
    assert claimed.json()["code"] == "482731"
    assert client.post(
        "/api/v1/otp/claim",
        json={"phone_number": "+15550100005"},
        headers=claim_headers,
    ).status_code == 404


def test_script_claim_requires_phone_number(client: TestClient) -> None:
    api_token = create_claim_client(client)
    response = client.post(
        "/api/v1/otp/claim",
        json={},
        headers={"Authorization": f"Bearer {api_token}"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "phone_number_required"


def test_invalid_script_auth_is_locked_after_five_failures(client: TestClient) -> None:
    from app.api import relay

    relay.script_auth_limiter.clear()
    try:
        headers = {"Authorization": "Bearer definitely-invalid-script-token"}
        statuses = [
            client.post(
                "/api/v1/otp/claim",
                json={"phone_number": "+15550100005"},
                headers=headers,
            ).status_code
            for _ in range(6)
        ]

        assert statuses[:5] == [401] * 5
        assert statuses[5] == 429
    finally:
        relay.script_auth_limiter.clear()


def test_invalid_hmac_device_never_creates_or_pushes_message(client: TestClient) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    payload = relay_payload("invalid-device-auth", source="root_module")
    raw, headers = hmac_request(
        "00000000-0000-0000-0000-000000000000",
        created["credential"]["secret"],
        payload,
        nonce="invalid-device-auth",
    )

    response = client.post("/api/v1/ingest/messages", content=raw, headers=headers)

    assert response.status_code == 401
    assert client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json() == []


def test_shortcut_ingest_accepts_message_object_and_builds_protocol_fields(
    client: TestClient,
) -> None:
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    token = created["credential"]["secret"]
    headers = {"Authorization": f"Bearer {token}"}
    message = {
        "Sender": "Authorized Relay Test",
        "Content": "Your relay access code is 482731",
        "Date": "2026-08-21T12:40:00+08:00",
    }

    response = client.post(
        "/api/v1/ingest/shortcut",
        json={"message": message},
        headers=headers,
    )

    assert response.status_code == 201
    listed = client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json()
    assert listed[0]["sender"] == "Authorized Relay Test"
    assert listed[0]["body"] == "Your relay access code is 482731"
    assert listed[0]["source"] == "ios_shortcut"
    assert listed[0]["client_message_id"].startswith("ios-shortcut-v1:")
    assert listed[0]["message_date"] == "2026-08-21T04:40:00+00:00"
    assert listed[0]["metadata"]["message_date_source"] == "device"
    assert listed[0]["created_at"]


def test_admin_message_metadata_never_exposes_a_plaintext_phone_number(
    client: TestClient,
) -> None:
    """Shortcut ingest stores the device's own number in ``metadata``.

    The admin API masks the same number as ``receiving_phone_masked``, so the
    raw value must not ride along in ``metadata`` — otherwise the masking the UI
    relies on is trivially bypassed by reading the other field.
    """
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    token = created["credential"]["secret"]
    payload = relay_payload("metadata-phone-mask-1")
    payload["metadata"]["phone_number"] = "+15550100005"

    ingested = client.post(
        "/api/v1/ingest/messages",
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "X-Idempotency-Key": "metadata-phone-mask-1",
        },
    )
    assert ingested.status_code == 201

    response = client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS)
    listed = response.json()
    assert listed[0]["metadata"]["phone_number"] == "********0005"
    assert listed[0]["receiving_phone_masked"] == "********0005"
    # The plaintext number must not leak anywhere in the serialized payload.
    assert "+15550100005" not in response.text


def test_shortcut_ingest_can_use_optional_receiving_phone_for_dual_sim(
    client: TestClient,
) -> None:
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    device_id = created["device"]["id"]
    token = created["credential"]["secret"]
    put_sim_profile(client, device_id, 0, subscription_id=None, phone_number="+15550100005")
    put_sim_profile(client, device_id, 1, subscription_id=None, phone_number="+15550100006")

    response = client.post(
        "/api/v1/ingest/shortcut",
        json={
            "message": {
                "Sender": "iPhone Messages",
                "Content": "Your code is 482731",
                "PhoneNumber": "+1 (555) 010-0006",
            }
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    listed = client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json()
    assert listed[0]["receiving_phone_masked"].endswith("0006")
    assert listed[0]["receiving_identity_status"] == "resolved"


def test_shortcut_ingest_replays_same_message_as_duplicate(client: TestClient) -> None:
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    token = created["credential"]["secret"]
    headers = {"Authorization": f"Bearer {token}"}
    body = {
        "message": {
            "Sender": "Authorized Relay Test",
            "Content": "Your relay access code is 482731",
            "Date": "2026-08-21T12:40:00+08:00",
        }
    }

    first = client.post("/api/v1/ingest/shortcut", json=body, headers=headers)
    second = client.post("/api/v1/ingest/shortcut", json=body, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert first.json()["message_id"] == second.json()["message_id"]


def test_shortcut_ingest_accepts_plain_text_message_input(client: TestClient) -> None:
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    token = created["credential"]["secret"]

    response = client.post(
        "/api/v1/ingest/shortcut",
        json={"message": "Your relay access code is 482731"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    listed = client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json()
    assert listed[0]["sender"] == "iPhone Messages"
    assert listed[0]["body"] == "Your relay access code is 482731"


def test_shortcut_ingest_accepts_single_localized_message_item(client: TestClient) -> None:
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    token = created["credential"]["secret"]

    response = client.post(
        "/api/v1/ingest/shortcut",
        json={
            "message": [
                {
                    "发件人": "授权中转测试",
                    "内容": "您的验证码是 482731",
                    "日期": "2026-08-21T12:40:00+08:00",
                }
            ]
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    listed = client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json()
    assert listed[0]["sender"] == "授权中转测试"
    assert listed[0]["body"] == "您的验证码是 482731"


def test_shortcut_plain_text_idempotency_uses_a_minute_bucket() -> None:
    # 幂等分钟桶锚定服务器时钟：iOS 重发同一短信时 received_at 各不相同
    # （重试跨分钟/设备时钟微差），桶不能依赖它，否则同一短信入库两次。
    first = build_shortcut_payload(
        "Your relay access code is 482731",
        received_at=datetime(2026, 8, 21, 4, 40, 10, tzinfo=timezone.utc),
    )
    retry_across_minutes = build_shortcut_payload(
        "Your relay access code is 482731",
        received_at=datetime(2026, 8, 21, 4, 41, 35, tzinfo=timezone.utc),
    )
    no_received_at = build_shortcut_payload(
        "Your relay access code is 482731",
    )

    assert first["client_message_id"] == retry_across_minutes["client_message_id"]
    assert first["client_message_id"] == no_received_at["client_message_id"]
    # received_at 仍按设备时间精确落库
    assert first["received_at"] == "2026-08-21T04:40:10+00:00"
    assert retry_across_minutes["received_at"] == "2026-08-21T04:41:35+00:00"


def test_shortcut_empty_message_date_uses_a_minute_bucket() -> None:
    first = build_shortcut_payload(
        {"Sender": "Relay", "Content": "Code 482731", "Date": "   "},
        received_at=datetime(2026, 8, 21, 4, 40, 10, tzinfo=timezone.utc),
    )
    later_message = build_shortcut_payload(
        {"Sender": "Relay", "Content": "Code 482731", "Date": "   "},
        received_at=datetime(2026, 8, 21, 4, 41, 10, tzinfo=timezone.utc),
    )

    # 服务器分钟桶：桶不再依赖 received_at，两次构造落在同一服务器分钟内
    # → 同一幂等键（这正是修复目标：设备时钟差异/跨分钟重试不再造成重复入库）
    assert first["client_message_id"] == later_message["client_message_id"]


def test_shortcut_normalizes_device_timestamp_and_falls_back_for_invalid_date() -> None:
    milliseconds = build_shortcut_payload(
        {"Content": "Code 482731", "Date": 1787287210000},
        received_at=datetime(2026, 8, 21, 4, 40, 10, tzinfo=timezone.utc),
    )
    invalid = build_shortcut_payload(
        {"Content": "Code 482731", "Date": "not-a-date"},
        received_at=datetime(2026, 8, 21, 4, 40, 10, tzinfo=timezone.utc),
    )

    assert milliseconds["metadata"]["message_date_source"] == "device"
    assert milliseconds["metadata"]["message_date"].endswith("+00:00")
    # 无效 Date 回退到 received_at 的设备时间分钟（用户配置了 received_at 时）
    assert invalid["metadata"]["message_date_source"] == "device_received_at"
    assert invalid["metadata"]["message_date"].endswith("+00:00")


def test_shortcut_accepts_device_generated_received_at_and_falls_back_when_invalid(
    client: TestClient,
) -> None:
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    token = created["credential"]["secret"]
    response = client.post(
        "/api/v1/ingest/shortcut",
        json={
            "message": {"Sender": "Relay", "Content": "Code 482731"},
            "received_at": "2026-09-02T06:42:29+08:00",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    stored = client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json()[0]
    assert stored["received_at"] == "2026-09-01T22:42:29+00:00"

    invalid = client.post(
        "/api/v1/ingest/shortcut",
        json={"message": "Code 927415", "received_at": "not-a-date"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert invalid.status_code == 201


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": ""},
        {"message": {"Sender": "Only sender"}},
        {"message": ["first", "second"]},
    ],
)
def test_shortcut_ingest_rejects_missing_message_content(
    client: TestClient,
    payload: dict[str, Any],
) -> None:
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    token = created["credential"]["secret"]

    response = client.post(
        "/api/v1/ingest/shortcut",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_message"


def test_shortcut_ingest_rejects_an_invalid_device_token(client: TestClient) -> None:
    response = client.post(
        "/api/v1/ingest/shortcut",
        json={"message": "Your relay access code is 482731"},
        headers={"Authorization": "Bearer invalid-device-token"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_device_token"


def test_hmac_nonce_replay_is_rejected_after_persistence(client: TestClient) -> None:
    created = create_device(client, client_type="android_app", auth_kind="hmac")
    device_id = created["device"]["id"]
    secret = created["credential"]["secret"]
    data = relay_payload("android-event-1", source="android_app")
    raw, headers = hmac_request(device_id, secret, data, nonce="neutral-test-nonce")

    assert client.post("/api/v1/ingest/messages", content=raw, headers=headers).status_code == 201
    assert client.post("/api/v1/ingest/messages", content=raw, headers=headers).status_code == 401


def test_ve_tweak_hmac_ingest_uses_the_dedicated_source_contract(
    client: TestClient,
) -> None:
    created = create_device(client, client_type="ve_tweak", auth_kind="hmac")
    device_id = created["device"]["id"]
    secret = created["credential"]["secret"]
    data = relay_payload("ve-event-1", body="Your verification code is 482731", source="ve_tweak")
    raw, headers = hmac_request(device_id, secret, data, nonce="ve-tweak-nonce-1")

    response = client.post("/api/v1/ingest/messages", content=raw, headers=headers)

    assert response.status_code == 201
    listed = client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS)
    assert listed.status_code == 200
    assert listed.json()[0]["device_id"] == device_id
    assert listed.json()[0]["source"] == "ve_tweak"
    assert listed.json()[0]["client_message_id"] == "ve-event-1"


def test_ve_tweak_rejects_a_source_from_another_device_contract(
    client: TestClient,
) -> None:
    created = create_device(client, client_type="ve_tweak", auth_kind="hmac")
    device_id = created["device"]["id"]
    data = relay_payload("ve-event-wrong-source", source="android_app")
    raw, headers = hmac_request(
        device_id,
        created["credential"]["secret"],
        data,
        nonce="ve-tweak-wrong-source-nonce",
    )

    response = client.post("/api/v1/ingest/messages", content=raw, headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_message"
    assert client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json() == []


@pytest.mark.parametrize("mutation", ["revoke", "restore", "purge"])
def test_hmac_ingest_cannot_persist_after_lifecycle_mutation(
    client: TestClient,
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = create_device(client, client_type="android_app", auth_kind="hmac")
    device_id = created["device"]["id"]
    payload = relay_payload(f"lifecycle-race-{mutation}", source="android_app")
    raw, headers = hmac_request(
        device_id,
        created["credential"]["secret"],
        payload,
        nonce=f"lifecycle-race-{mutation}",
    )
    entered = threading.Event()
    allow_persistence = threading.Event()
    original_ingest = store.ingest

    def paused_ingest(*args: Any, **kwargs: Any) -> tuple[Any, bool]:
        entered.set()
        assert allow_persistence.wait(timeout=5)
        return original_ingest(*args, **kwargs)

    monkeypatch.setattr(store, "ingest", paused_ingest)
    with ThreadPoolExecutor(max_workers=1) as executor:
        request_future = executor.submit(
            lambda: client.post("/api/v1/ingest/messages", content=raw, headers=headers)
        )
        assert entered.wait(timeout=5)

        parsed_device_id = UUID(device_id)
        store.revoke_device(parsed_device_id)
        if mutation == "restore":
            store.restore_device(parsed_device_id)
        elif mutation == "purge":
            store.purge_device(parsed_device_id)
        allow_persistence.set()
        response = request_future.result(timeout=5)

    assert response.status_code == 401
    with store.session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(MessageModel).where(MessageModel.device_id == device_id)
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(DeviceNonceModel).where(DeviceNonceModel.device_id == device_id)
        ) == 0
        assert session.scalar(select(func.count()).select_from(OtpModel)) == 0


@pytest.mark.parametrize("field", ["name", "client_type", "auth_kind"])
@pytest.mark.parametrize("value", [[], {}])
def test_device_enrollment_rejects_non_primitive_fields(
    client: TestClient,
    field: str,
    value: list[Any] | dict[str, Any],
) -> None:
    payload: dict[str, Any] = {
        "name": "typed device",
        "client_type": "android_app",
        "auth_kind": "hmac",
    }
    payload[field] = value

    response = client.post("/api/v1/admin/devices", json=payload, headers=ADMIN_HEADERS)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_device"


def test_inactive_devices_cannot_rotate_credentials(client: TestClient) -> None:
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    device_id = created["device"]["id"]
    assert client.delete(f"/api/v1/admin/devices/{device_id}", headers=ADMIN_HEADERS).status_code == 204

    response = client.post(
        f"/api/v1/admin/devices/{device_id}/rotate-credential", headers=ADMIN_HEADERS
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "device_not_active"


@pytest.mark.parametrize(
    ("client_type", "auth_kind", "source"),
    [
        ("ios_shortcut", "bearer", "ios_shortcut"),
        ("android_app", "hmac", "android_app"),
    ],
)
def test_restore_returns_a_new_credential_and_keeps_the_old_one_invalid(
    client: TestClient,
    client_type: str,
    auth_kind: str,
    source: str,
) -> None:
    created = create_device(client, client_type=client_type, auth_kind=auth_kind)
    device_id = created["device"]["id"]
    old_secret = created["credential"]["secret"]
    assert client.delete(f"/api/v1/admin/devices/{device_id}", headers=ADMIN_HEADERS).status_code == 204

    restored = client.post(
        f"/api/v1/admin/devices/{device_id}/restore",
        headers=ADMIN_HEADERS,
    )

    assert restored.status_code == 200
    assert restored.json()["device"]["active"] is True
    new_secret = restored.json()["credential"]["secret"]
    assert new_secret != old_secret

    old_payload = relay_payload(f"restore-old-{client_type}", source=source)
    new_payload = relay_payload(f"restore-new-{client_type}", source=source)
    if auth_kind == "bearer":
        old_response = client.post(
            "/api/v1/ingest/messages",
            json=old_payload,
            headers={
                "Authorization": f"Bearer {old_secret}",
                "X-Idempotency-Key": old_payload["client_message_id"],
            },
        )
        new_response = client.post(
            "/api/v1/ingest/messages",
            json=new_payload,
            headers={
                "Authorization": f"Bearer {new_secret}",
                "X-Idempotency-Key": new_payload["client_message_id"],
            },
        )
    else:
        old_raw, old_headers = hmac_request(
            device_id,
            old_secret,
            old_payload,
            nonce=f"restore-old-{client_type}",
        )
        new_raw, new_headers = hmac_request(
            device_id,
            new_secret,
            new_payload,
            nonce=f"restore-new-{client_type}",
        )
        old_response = client.post(
            "/api/v1/ingest/messages", content=old_raw, headers=old_headers
        )
        new_response = client.post(
            "/api/v1/ingest/messages", content=new_raw, headers=new_headers
        )

    assert old_response.status_code == 401
    assert new_response.status_code == 201


def test_active_devices_cannot_be_restored_or_purged(client: TestClient) -> None:
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    device_id = created["device"]["id"]

    restore = client.post(
        f"/api/v1/admin/devices/{device_id}/restore",
        headers=ADMIN_HEADERS,
    )
    purge = client.delete(
        f"/api/v1/admin/devices/{device_id}/purge",
        headers=ADMIN_HEADERS,
    )

    assert restore.status_code == 409
    assert restore.json()["error"]["code"] == "device_not_revoked"
    assert purge.status_code == 409
    assert purge.json()["error"]["code"] == "device_not_revoked"


def test_purge_removes_revoked_device_data_and_keeps_a_non_sensitive_audit(
    client: TestClient,
) -> None:
    created = create_device(client, client_type="android_app", auth_kind="hmac")
    device_id = created["device"]["id"]
    payload = relay_payload("purge-cascade-1", source="android_app")
    raw, headers = hmac_request(
        device_id,
        created["credential"]["secret"],
        payload,
        nonce="purge-cascade-nonce",
    )
    ingested = client.post("/api/v1/ingest/messages", content=raw, headers=headers)
    assert ingested.status_code == 201
    profile = client.put(
        f"/api/v1/admin/devices/{device_id}/sim-profiles/0",
        json={
            "subscription_id": 1042,
            "phone_number": "5550101234",
            "carrier_name": "Neutral Carrier",
            "source": "manual",
        },
        headers=ADMIN_HEADERS,
    )
    assert profile.status_code == 200
    assert client.delete(f"/api/v1/admin/devices/{device_id}", headers=ADMIN_HEADERS).status_code == 204

    purged = client.delete(
        f"/api/v1/admin/devices/{device_id}/purge",
        headers=ADMIN_HEADERS,
    )

    assert purged.status_code == 204
    assert all(item["id"] != device_id for item in client.get("/api/v1/admin/devices", headers=ADMIN_HEADERS).json())
    assert client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json() == []
    assert client.get("/api/v1/admin/otps", headers=ADMIN_HEADERS).json() == []
    with store.session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(DeviceModel).where(DeviceModel.id == device_id)
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(DeviceNonceModel)
            .where(DeviceNonceModel.device_id == device_id)
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(DeviceSimProfileModel)
            .where(DeviceSimProfileModel.device_id == device_id)
        ) == 0
        assert session.scalar(
            select(func.count())
            .select_from(MessageModel)
            .where(MessageModel.device_id == device_id)
        ) == 0
        assert session.scalar(select(func.count()).select_from(OtpModel)) == 0
        audit = session.scalar(
            select(AuditEventModel)
            .where(
                AuditEventModel.event_type == "device.purged",
                AuditEventModel.entity_id == device_id,
            )
            .order_by(AuditEventModel.id.desc())
        )
        assert audit is not None
        assert audit.details == {}


def test_admin_sim_profile_upsert_list_get_update_and_delete_are_masked(
    client: TestClient,
) -> None:
    created = create_device(client, client_type="android_app", auth_kind="hmac")
    device_id = created["device"]["id"]
    raw_phone = "+1 (555) 010-1234"
    normalized_phone = "+15550101234"

    created_profile = client.put(
        f"/api/v1/admin/devices/{device_id}/sim-profiles/0",
        json={
            "subscription_id": 1042,
            "phone_number": raw_phone,
            "carrier_name": "Neutral Carrier",
            "source": "auto",
        },
        headers=ADMIN_HEADERS,
    )

    assert created_profile.status_code == 200
    assert created_profile.json() == {
        "device_id": device_id,
        "slot_index": 0,
        "subscription_id": 1042,
        "phone_number_masked": "********1234",
        "carrier_name": "Neutral Carrier",
        "source": "auto",
        "updated_at": created_profile.json()["updated_at"],
    }
    assert "phone_number" not in created_profile.json()
    assert raw_phone not in created_profile.text

    listed = client.get(
        f"/api/v1/admin/devices/{device_id}/sim-profiles",
        headers=ADMIN_HEADERS,
    )
    single = client.get(
        f"/api/v1/admin/devices/{device_id}/sim-profiles/0",
        headers=ADMIN_HEADERS,
    )

    assert listed.status_code == 200
    assert listed.json() == [single.json()]
    assert single.status_code == 200
    assert single.json()["phone_number_masked"] == "********1234"
    assert "phone_number" not in single.json()
    assert raw_phone not in listed.text

    updated = client.put(
        f"/api/v1/admin/devices/{device_id}/sim-profiles/0",
        json={"carrier_name": "Updated Carrier", "source": "vendor"},
        headers=ADMIN_HEADERS,
    )

    assert updated.status_code == 200
    assert updated.json()["phone_number_masked"] == "********1234"
    assert updated.json()["carrier_name"] == "Updated Carrier"
    assert updated.json()["source"] == "vendor"
    with store.session_factory() as session:
        stored = session.scalar(
            select(DeviceSimProfileModel).where(
                DeviceSimProfileModel.device_id == device_id,
                DeviceSimProfileModel.slot_index == 0,
            )
        )
        assert stored is not None
        assert not hasattr(stored, "phone_number")
        assert stored.phone_number_nonce is not None
        assert stored.phone_number_ciphertext is not None
        assert stored.phone_number_key_version == 1
        assert raw_phone.encode() not in stored.phone_number_ciphertext
        assert normalized_phone.encode() not in stored.phone_number_ciphertext
        encrypted = EncryptedValue(
            stored.phone_number_nonce,
            stored.phone_number_ciphertext,
            stored.phone_number_key_version,
        )
        aad = f"device-sim-profile:{device_id}:0:phone:v1"
        assert store.crypto.decrypt_text(encrypted, aad) == normalized_phone
        with pytest.raises(InvalidTag):
            store.crypto.decrypt_text(
                encrypted,
                f"device-sim-profile:{device_id}:1:phone:v1",
            )

    cleared = client.put(
        f"/api/v1/admin/devices/{device_id}/sim-profiles/0",
        json={"phone_number": None},
        headers=ADMIN_HEADERS,
    )
    assert cleared.status_code == 200
    assert cleared.json()["phone_number_masked"] is None

    deleted = client.delete(
        f"/api/v1/admin/devices/{device_id}/sim-profiles/0",
        headers=ADMIN_HEADERS,
    )
    assert deleted.status_code == 204
    assert client.get(
        f"/api/v1/admin/devices/{device_id}/sim-profiles/0",
        headers=ADMIN_HEADERS,
    ).status_code == 404


def test_admin_sim_profile_fully_masks_phone_numbers_without_a_safe_suffix(
    client: TestClient,
) -> None:
    created = create_device(client, client_type="android_app", auth_kind="hmac")
    device_id = created["device"]["id"]

    for raw_phone, expected_mask in [("1", "*"), ("12-34", "****")]:
        response = client.put(
            f"/api/v1/admin/devices/{device_id}/sim-profiles/0",
            json={"phone_number": raw_phone, "source": "manual"},
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 200
        assert response.json()["phone_number_masked"] == expected_mask
        assert response.json()["phone_number_masked"] != raw_phone
        assert "phone_number" not in response.json()


def test_admin_sim_profile_masks_normalized_leading_plus_by_digit_count(
    client: TestClient,
) -> None:
    created = create_device(client, client_type="android_app", auth_kind="hmac")
    device_id = created["device"]["id"]

    cases = [("+1", "**"), ("+1234", "*****"), ("+12345", "**2345")]
    for raw_phone, expected_mask in cases:
        response = client.put(
            f"/api/v1/admin/devices/{device_id}/sim-profiles/0",
            json={"phone_number": raw_phone, "source": "manual"},
            headers=ADMIN_HEADERS,
        )

        assert response.status_code == 200
        assert response.json()["phone_number_masked"] == expected_mask
        assert response.json()["phone_number_masked"] != raw_phone
        assert "phone_number" not in response.json()


@pytest.mark.parametrize("raw_phone", ["", "   ", "*", "*1234", "****", "()-"])
def test_admin_sim_profile_rejects_invalid_phone_numbers(
    client: TestClient,
    raw_phone: str,
) -> None:
    created = create_device(client, client_type="android_app", auth_kind="hmac")
    device_id = created["device"]["id"]

    response = client.put(
        f"/api/v1/admin/devices/{device_id}/sim-profiles/0",
        json={"phone_number": raw_phone, "source": "manual"},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_sim_profile"


@pytest.mark.parametrize("source", ["", "other", None, 1, [], {}])
def test_admin_sim_profile_rejects_invalid_source(client: TestClient, source: Any) -> None:
    created = create_device(client, client_type="android_app", auth_kind="hmac")
    device_id = created["device"]["id"]

    response = client.put(
        f"/api/v1/admin/devices/{device_id}/sim-profiles/0",
        json={"source": source},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_sim_profile"


@pytest.mark.parametrize("slot", [-1, 2_147_483_648])
def test_admin_sim_profile_rejects_out_of_range_slot(client: TestClient, slot: int) -> None:
    created = create_device(client, client_type="android_app", auth_kind="hmac")
    device_id = created["device"]["id"]

    response = client.put(
        f"/api/v1/admin/devices/{device_id}/sim-profiles/{slot}",
        json={"source": "manual"},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_sim_profile"


@pytest.mark.parametrize("subscription_id", [-2_147_483_649, 2_147_483_648])
def test_admin_sim_profile_rejects_out_of_range_subscription_id(
    client: TestClient,
    subscription_id: int,
) -> None:
    created = create_device(client, client_type="android_app", auth_kind="hmac")
    device_id = created["device"]["id"]

    response = client.put(
        f"/api/v1/admin/devices/{device_id}/sim-profiles/0",
        json={"subscription_id": subscription_id, "source": "manual"},
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_sim_profile"


def test_concurrent_sim_profile_upserts_use_database_serialization(database_url: str) -> None:
    first = RelayStore()
    second = RelayStore()
    first.configure(database_url, drop_existing=True)
    second.configure(database_url)
    device, _ = first.create_device("Concurrent SIM device", "android_app", "hmac")
    start_barrier = threading.Barrier(2)
    read_barrier = threading.Barrier(2)

    def synchronize_profile_reads(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT") and "device_sim_profiles" in statement:
            try:
                read_barrier.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass

    event.listen(first.engine, "after_cursor_execute", synchronize_profile_reads)
    event.listen(second.engine, "after_cursor_execute", synchronize_profile_reads)

    def write_profile(relay_store: RelayStore, carrier_name: str) -> object:
        start_barrier.wait(timeout=2)
        try:
            return relay_store.upsert_sim_profile(
                device.id,
                0,
                {
                    "subscription_id": 1042,
                    "phone_number": "5550101234",
                    "carrier_name": carrier_name,
                    "source": "manual",
                },
            )
        except Exception as exc:  # pragma: no cover - the assertion reports only the type
            return exc

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(write_profile, first, "Carrier A"),
                executor.submit(write_profile, second, "Carrier B"),
            ]
            results = [future.result(timeout=5) for future in futures]

        errors = [result for result in results if isinstance(result, Exception)]
        assert errors == [], [type(error).__name__ for error in errors]
        profiles = first.list_sim_profiles(device.id)
        assert len(profiles) == 1
        assert profiles[0].carrier_name in {"Carrier A", "Carrier B"}
    finally:
        first.dispose()
        second.dispose()


def test_admin_sim_profiles_require_an_existing_device_and_slot(client: TestClient) -> None:
    missing_device = "00000000-0000-0000-0000-000000000000"
    created = create_device(client, client_type="android_app", auth_kind="hmac")
    device_id = created["device"]["id"]

    assert client.get(
        f"/api/v1/admin/devices/{missing_device}/sim-profiles",
        headers=ADMIN_HEADERS,
    ).status_code == 404
    assert client.put(
        f"/api/v1/admin/devices/{missing_device}/sim-profiles/0",
        json={"source": "manual"},
        headers=ADMIN_HEADERS,
    ).status_code == 404

    missing_get = client.get(
        f"/api/v1/admin/devices/{device_id}/sim-profiles/0",
        headers=ADMIN_HEADERS,
    )
    missing_delete = client.delete(
        f"/api/v1/admin/devices/{device_id}/sim-profiles/0",
        headers=ADMIN_HEADERS,
    )
    assert missing_get.status_code == 404
    assert missing_get.json()["error"]["code"] == "sim_profile_not_found"
    assert missing_delete.status_code == 404
    assert missing_delete.json()["error"]["code"] == "sim_profile_not_found"


@pytest.mark.parametrize(
    ("client_type", "auth_kind", "source"),
    [
        ("android_app", "hmac", "android_app"),
        ("root_module", "hmac", "root_module"),
        ("ios_shortcut", "bearer", "ios_shortcut"),
        ("ve_tweak", "hmac", "ve_tweak"),
    ],
)
def test_supported_device_contracts_accept_their_matching_source(
    client: TestClient,
    client_type: str,
    auth_kind: str,
    source: str,
) -> None:
    created = create_device(client, client_type=client_type, auth_kind=auth_kind)
    device_id = created["device"]["id"]
    secret = created["credential"]["secret"]
    payload = relay_payload(f"contract-{client_type}", source=source)
    if auth_kind == "bearer":
        response = client.post(
            "/api/v1/ingest/messages",
            json=payload,
            headers={
                "Authorization": f"Bearer {secret}",
                "X-Idempotency-Key": payload["client_message_id"],
            },
        )
    else:
        raw, headers = hmac_request(
            device_id,
            secret,
            payload,
            nonce=f"contract-{client_type}",
        )
        response = client.post("/api/v1/ingest/messages", content=raw, headers=headers)

    assert response.status_code == 201


def test_device_rotation_and_revocation_invalidate_old_credentials(client: TestClient) -> None:
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    device_id = created["device"]["id"]
    old_token = created["credential"]["secret"]
    rotated = client.post(
        f"/api/v1/admin/devices/{device_id}/rotate-credential", headers=ADMIN_HEADERS
    )
    assert rotated.status_code == 200
    new_token = rotated.json()["credential"]["secret"]
    assert new_token != old_token

    old_headers = {"Authorization": f"Bearer {old_token}", "X-Idempotency-Key": "rotated-1"}
    new_headers = {"Authorization": f"Bearer {new_token}", "X-Idempotency-Key": "rotated-1"}
    assert client.post(
        "/api/v1/ingest/messages", json=relay_payload("rotated-1"), headers=old_headers
    ).status_code == 401
    assert client.post(
        "/api/v1/ingest/messages", json=relay_payload("rotated-1"), headers=new_headers
    ).status_code == 201

    assert client.delete(f"/api/v1/admin/devices/{device_id}", headers=ADMIN_HEADERS).status_code == 204
    revoked_headers = {"Authorization": f"Bearer {new_token}", "X-Idempotency-Key": "revoked-1"}
    assert client.post(
        "/api/v1/ingest/messages", json=relay_payload("revoked-1"), headers=revoked_headers
    ).status_code == 401


def test_retention_redacts_sensitive_fields_but_keeps_record(client: TestClient) -> None:
    from app.services.store import store

    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    token = created["credential"]["secret"]
    headers = {"Authorization": f"Bearer {token}", "X-Idempotency-Key": "retention-event-1"}
    response = client.post(
        "/api/v1/ingest/messages", json=relay_payload("retention-event-1"), headers=headers
    )
    assert response.status_code == 201

    store.age_message(response.json()["message_id"], days=8)
    purged = client.post("/api/v1/admin/retention/run", headers=ADMIN_HEADERS)
    assert purged.status_code == 200 and purged.json()["redacted_messages"] == 1

    listed = client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json()
    message = next(item for item in listed if item["client_message_id"] == "retention-event-1")
    assert message["redacted"] is True
    assert message["body"] is None
    assert message["metadata"] == {}


def test_message_keyword_filter_handles_redacted_message_without_crashing(client: TestClient) -> None:
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    token = created["credential"]["secret"]
    response = client.post(
        "/api/v1/ingest/messages",
        json=relay_payload("retention-filter-event-1"),
        headers={"Authorization": f"Bearer {token}", "X-Idempotency-Key": "retention-filter-event-1"},
    )
    assert response.status_code == 201
    store.age_message(response.json()["message_id"], days=8)
    assert client.post("/api/v1/admin/retention/run", headers=ADMIN_HEADERS).status_code == 200

    filtered = client.get(
        "/api/v1/admin/messages",
        params={"keyword": "access"},
        headers=ADMIN_HEADERS,
    )
    assert filtered.status_code == 200
    assert filtered.json() == []


def test_database_state_survives_store_reconfiguration(client: TestClient, database_url: str) -> None:
    from app.services.store import store

    create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    store.configure(database_url)
    assert len(store.list_devices()) == 1


def test_concurrent_claim_returns_code_once(client: TestClient) -> None:
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    token = created["credential"]["secret"]
    ingest_headers = {"Authorization": f"Bearer {token}", "X-Idempotency-Key": "claim-race-1"}
    race_payload = relay_payload("claim-race-1")
    race_payload["metadata"]["phone_number"] = "+15550100005"
    assert client.post(
        "/api/v1/ingest/messages", json=race_payload, headers=ingest_headers
    ).status_code == 201
    api_token = create_claim_client(client)
    claim_headers = {"Authorization": f"Bearer {api_token}"}

    def claim() -> int:
        return cast(
            int,
            client.post(
                "/api/v1/otp/claim",
                json={"phone_number": "+15550100005"},
                headers=claim_headers,
            ).status_code,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(lambda _: claim(), range(2)))
    assert statuses == [200, 404]


def test_expired_code_is_deleted_and_is_not_claimable(client: TestClient) -> None:
    from app.services.store import store

    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    token = created["credential"]["secret"]
    headers = {"Authorization": f"Bearer {token}", "X-Idempotency-Key": "expired-event-1"}
    response = client.post(
        "/api/v1/ingest/messages", json=relay_payload("expired-event-1"), headers=headers
    )
    assert response.status_code == 201
    store.age_message(response.json()["message_id"], seconds=1)
    api_token = create_claim_client(client)
    claim_headers = {"Authorization": f"Bearer {api_token}"}
    assert client.post(
        "/api/v1/otp/claim",
        json={"phone_number": "+15550100005"},
        headers=claim_headers,
    ).status_code == 404
    assert client.get("/api/v1/admin/otps", headers=ADMIN_HEADERS).json() == []


def test_claim_returns_the_newest_available_code_for_a_phone(client: TestClient) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    put_sim_profile(client, device_id, 0, subscription_id=5, phone_number="+15550100005")
    first = ingest_root_message(
        client,
        created,
        "same-phone-old",
        nonce="same-phone-old",
        sim_slot=0,
        metadata={"subscription_id": 5},
        body="Your one-time access code is 111111",
    )
    second = ingest_root_message(
        client,
        created,
        "same-phone-new",
        nonce="same-phone-new",
        sim_slot=0,
        metadata={"subscription_id": 5},
        body="Your one-time access code is 222222",
    )
    api_token = create_claim_client(client)
    claimed = client.post(
        "/api/v1/otp/claim",
        json={"phone_number": "+15550100005"},
        headers={"Authorization": f"Bearer {api_token}"},
    )

    assert first["message_id"] != second["message_id"]
    assert claimed.status_code == 200
    assert claimed.json()["code"] == "222222"
    assert claimed.json()["message_id"] == second["message_id"]
    assert claimed.json()["created_at"]
    older = client.post(
        "/api/v1/otp/claim",
        json={"phone_number": "+15550100005"},
        headers={"Authorization": f"Bearer {api_token}"},
    )
    assert older.status_code == 200
    assert older.json()["code"] == "111111"


def test_claim_keeps_older_valid_matching_code_for_disconnect_recovery(client: TestClient) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    put_sim_profile(client, device_id, 0, subscription_id=5, phone_number="+15550100005")
    ingest_root_message(
        client,
        created,
        "claim-preserve-old",
        nonce="claim-preserve-old",
        sim_slot=0,
        metadata={"subscription_id": 5},
        body="Your one-time access code is 111111",
    )
    ingest_root_message(
        client,
        created,
        "claim-preserve-new",
        nonce="claim-preserve-new",
        sim_slot=0,
        metadata={"subscription_id": 5},
        body="Your one-time access code is 222222",
    )
    api_token = create_claim_client(client)
    headers = {"Authorization": f"Bearer {api_token}"}

    newest = client.post(
        "/api/v1/otp/claim",
        json={"phone_number": "+15550100005"},
        headers=headers,
    )
    older = client.post(
        "/api/v1/otp/claim",
        json={"phone_number": "+15550100005"},
        headers=headers,
    )

    assert newest.status_code == 200
    assert newest.json()["code"] == "222222"
    assert newest.json()["created_at"]
    assert older.status_code == 200
    assert older.json()["code"] == "111111"


def test_claim_sender_filter_matches_websocket_substring_semantics(client: TestClient) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    put_sim_profile(client, device_id, 0, subscription_id=5, phone_number="+15550100005")
    ingest_root_message(
        client,
        created,
        "claim-sender-substring",
        nonce="claim-sender-substring",
        sim_slot=0,
        metadata={"subscription_id": 5},
        sender="Relay Test Service",
        body="Your access code is 482731",
    )
    api_token = create_claim_client(client)

    claimed = client.post(
        "/api/v1/otp/claim",
        json={"phone_number": "+15550100005", "sender": "relay test"},
        headers={"Authorization": f"Bearer {api_token}"},
    )

    assert claimed.status_code == 200
    assert claimed.json()["code"] == "482731"


def test_otp_websocket_rejects_invalid_script_token(client: TestClient) -> None:
    with client.websocket_connect("/api/v1/ws/otp") as websocket:
        websocket.send_json({"type": "auth", "token": "invalid-script-token"})
        assert websocket.receive_json() == {
            "type": "error",
            "code": "invalid_api_client",
            "message": "Script API token is invalid",
        }


def test_otp_websocket_auth_timeout_is_bounded(client: TestClient) -> None:
    from app.api import relay

    previous = relay.WS_AUTH_TIMEOUT_SECONDS
    relay.WS_AUTH_TIMEOUT_SECONDS = 0.01
    try:
        with client.websocket_connect("/api/v1/ws/otp") as websocket:
            assert websocket.receive_json() == {
                "type": "error",
                "code": "auth_timeout",
                "message": "Authentication frame timed out",
            }
    finally:
        relay.WS_AUTH_TIMEOUT_SECONDS = previous


def test_otp_websocket_subscription_timeout_is_bounded(client: TestClient) -> None:
    from app.api import relay

    api_token = create_claim_client(client)
    previous = relay.WS_SUBSCRIPTION_TIMEOUT_SECONDS
    relay.WS_SUBSCRIPTION_TIMEOUT_SECONDS = 0.01
    try:
        with client.websocket_connect("/api/v1/ws/otp") as websocket:
            websocket.send_json({"type": "auth", "token": api_token})
            assert websocket.receive_json() == {"type": "auth_ok"}
            assert websocket.receive_json() == {
                "type": "error",
                "code": "subscription_timeout",
                "message": "Subscription frame timed out",
            }
    finally:
        relay.WS_SUBSCRIPTION_TIMEOUT_SECONDS = previous


def test_otp_websocket_pushes_new_matching_code_without_claiming_it(client: TestClient) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    put_sim_profile(client, device_id, 0, subscription_id=5, phone_number="+15550100005")
    api_token = create_claim_client(client)

    with client.websocket_connect("/api/v1/ws/otp") as websocket:
        websocket.send_json({"type": "auth", "token": api_token})
        assert websocket.receive_json()["type"] == "auth_ok"
        websocket.send_json(
            {
                "type": "subscribe",
                "phone_number": "+1 (555) 010-0005",
                "device_id": device_id,
                "sender": "Service",
                "keyword": "access code",
            }
        )
        subscribed = websocket.receive_json()
        assert subscribed["type"] == "subscribed"
        assert subscribed["phone_number"] == "+15550100005"

        ingested = ingest_root_message(
            client,
            created,
            "websocket-live-otp",
            nonce="websocket-live-otp",
            sim_slot=0,
            metadata={"subscription_id": 5},
            sender="Service Relay",
            body="Your access code is 482731",
        )
        event = websocket.receive_json()

    assert event["type"] == "otp"
    assert event["message_id"] == ingested["message_id"]
    assert event["code"] == "482731"
    assert event["phone_number"] == "+15550100005"
    assert "body" not in event

    claimed = client.post(
        "/api/v1/otp/claim",
        json={"phone_number": "+15550100005"},
        headers={"Authorization": f"Bearer {api_token}"},
    )
    assert claimed.status_code == 200
    assert claimed.json()["code"] == "482731"


def test_otp_websocket_rejects_a_second_subscription(client: TestClient) -> None:
    api_token = create_claim_client(client)

    with client.websocket_connect("/api/v1/ws/otp") as websocket:
        websocket.send_json({"type": "auth", "token": api_token})
        assert websocket.receive_json()["type"] == "auth_ok"
        websocket.send_json({"type": "subscribe", "phone_number": "+15550100005"})
        assert websocket.receive_json()["type"] == "subscribed"
        websocket.send_json({"type": "subscribe", "phone_number": "+15550100006"})
        assert websocket.receive_json() == {
            "type": "error",
            "code": "subscription_already_set",
            "message": "Only one subscription is allowed per connection",
        }


def test_otp_websocket_rejects_oversized_control_frame(client: TestClient) -> None:
    from app.api import relay

    api_token = create_claim_client(client)
    previous = relay.MAX_WS_FRAME_BYTES
    relay.MAX_WS_FRAME_BYTES = 256
    try:
        with client.websocket_connect("/api/v1/ws/otp") as websocket:
            websocket.send_json({"type": "auth", "token": api_token})
            assert websocket.receive_json()["type"] == "auth_ok"
            websocket.send_json(
                {"type": "subscribe", "phone_number": "+15550100005", "keyword": "x" * 1000}
            )
            assert websocket.receive_json() == {
                "type": "error",
                "code": "frame_too_large",
                "message": "WebSocket control frame is too large",
            }
    finally:
        relay.MAX_WS_FRAME_BYTES = previous


def test_otp_websocket_does_not_push_duplicate_http_ingest(client: TestClient) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    put_sim_profile(client, device_id, 0, subscription_id=5, phone_number="+15550100005")
    api_token = create_claim_client(client)
    payload = relay_payload("websocket-duplicate-otp", source="root_module")
    payload["sim_slot"] = 0
    payload["metadata"] = {"subscription_id": 5}
    raw, headers = hmac_request(
        device_id,
        created["credential"]["secret"],
        payload,
        nonce="websocket-duplicate-otp",
    )

    with client.websocket_connect("/api/v1/ws/otp") as websocket:
        websocket.send_json({"type": "auth", "token": api_token})
        assert websocket.receive_json()["type"] == "auth_ok"
        websocket.send_json({"type": "subscribe", "phone_number": "+15550100005"})
        assert websocket.receive_json()["type"] == "subscribed"

        first = client.post("/api/v1/ingest/messages", content=raw, headers=headers)
        assert first.status_code == 201
        assert websocket.receive_json()["type"] == "otp"

        second_raw, second_headers = hmac_request(
            device_id,
            created["credential"]["secret"],
            payload,
            nonce="websocket-duplicate-otp-retry",
        )
        second = client.post(
            "/api/v1/ingest/messages",
            content=second_raw,
            headers=second_headers,
        )
        assert second.status_code == 200
        assert second.json()["duplicate"] is True
        websocket.send_json({"type": "ping"})
        assert websocket.receive_json() == {"type": "pong"}


def test_claim_uses_server_arrival_order_when_device_clock_is_skewed(client: TestClient) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    put_sim_profile(client, device_id, 0, subscription_id=5, phone_number="+15550100005")
    first = ingest_root_message(
        client,
        created,
        "skewed-arrival-old",
        nonce="skewed-arrival-old",
        sim_slot=0,
        metadata={"subscription_id": 5},
        body="Your one-time access code is 111111",
    )
    second = ingest_root_message(
        client,
        created,
        "skewed-arrival-new",
        nonce="skewed-arrival-new",
        sim_slot=0,
        metadata={"subscription_id": 5},
        body="Your one-time access code is 222222",
    )

    # The device clock can be behind or ahead of the server. Claiming must use
    # server insertion order, not a client-provided SMS timestamp.
    with store.session_factory.begin() as session:
        session.execute(
            update(MessageModel)
            .where(MessageModel.id == first["message_id"])
            .values(received_at=datetime.now(timezone.utc) + timedelta(hours=1))
        )

    api_token = create_claim_client(client)
    claimed = client.post(
        "/api/v1/otp/claim",
        json={"phone_number": "+15550100005"},
        headers={"Authorization": f"Bearer {api_token}"},
    )

    assert claimed.status_code == 200
    assert claimed.json()["code"] == "222222"
    assert claimed.json()["message_id"] == second["message_id"]


def test_claimed_code_is_retained_for_one_day_then_deleted(client: TestClient) -> None:
    from app.core.config import settings

    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    token = created["credential"]["secret"]
    retention_payload = relay_payload("claimed-retention-1")
    retention_payload["metadata"]["phone_number"] = "+15550100005"
    ingested = client.post(
        "/api/v1/ingest/messages",
        json=retention_payload,
        headers={"Authorization": f"Bearer {token}", "X-Idempotency-Key": "claimed-retention-1"},
    )
    api_token = create_claim_client(client)
    claim_headers = {"Authorization": f"Bearer {api_token}"}
    claimed = client.post(
        "/api/v1/otp/claim",
        json={"phone_number": "+15550100005"},
        headers=claim_headers,
    )
    assert ingested.status_code == 201
    assert claimed.status_code == 200
    otp_id = claimed.json()["otp_id"]
    admin_items = client.get("/api/v1/admin/otps", headers=ADMIN_HEADERS).json()
    assert admin_items[0]["message_id"] == ingested.json()["message_id"]
    assert admin_items[0]["status"] == "claimed"
    assert "body" not in admin_items[0]

    with store.session_factory.begin() as session:
        session.execute(
            update(OtpModel)
            .where(OtpModel.id == otp_id)
            .values(
                claimed_at=datetime.now(timezone.utc)
                - timedelta(seconds=settings.claimed_otp_retention_seconds + 1)
            )
        )

    assert client.post("/api/v1/admin/retention/run", headers=ADMIN_HEADERS).status_code == 200
    assert client.get("/api/v1/admin/otps", headers=ADMIN_HEADERS).json() == []


def test_device_type_authentication_pair_is_enforced(client: TestClient) -> None:
    response = client.post(
        "/api/v1/admin/devices",
        json={
            "name": "Invalid pairing",
            "client_type": "android_app",
            "auth_kind": "bearer",
        },
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_device"


def test_ingest_rejects_source_size_and_timestamp_mismatches(client: TestClient) -> None:
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    token = created["credential"]["secret"]

    def submit(client_id: str, payload: dict[str, Any]) -> int:
        return cast(
            int,
            client.post(
                "/api/v1/ingest/messages",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Idempotency-Key": client_id,
                },
            ).status_code,
        )

    wrong_source = relay_payload("source-mismatch", source="android_app")
    oversized_body = relay_payload("oversized-body", body="x" * 4097)
    oversized_metadata = relay_payload("oversized-metadata")
    oversized_metadata["metadata"] = {"note": "x" * 8192}
    naive_timestamp = relay_payload("naive-time")
    naive_timestamp["received_at"] = "2026-08-16T12:00:00"

    assert submit("source-mismatch", wrong_source) == 422
    assert submit("oversized-body", oversized_body) == 422
    assert submit("oversized-metadata", oversized_metadata) == 422
    assert submit("naive-time", naive_timestamp) == 422


def test_ingest_rejects_oversized_http_body_before_json_processing(client: TestClient) -> None:
    response = client.post(
        "/api/v1/ingest/messages",
        content=b"{" + b"x" * (128 * 1024) + b"}",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_retention_removes_expired_hmac_nonces(client: TestClient) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    with store.session_factory.begin() as session:
        session.add(DeviceNonceModel(device_id=device_id, nonce="old-nonce"))
        session.add(DeviceNonceModel(device_id=device_id, nonce="new-nonce"))
        session.flush()
        session.execute(
            update(DeviceNonceModel)
            .where(DeviceNonceModel.device_id == device_id, DeviceNonceModel.nonce == "old-nonce")
            .values(created_at=datetime.now(timezone.utc).replace(year=2020))
        )

    store.run_retention(7)

    with store.session_factory() as session:
        nonces = session.scalars(
            select(DeviceNonceModel.nonce).where(DeviceNonceModel.device_id == device_id)
        ).all()
    assert nonces == ["new-nonce"]


def ingest_root_message(
    client: TestClient,
    created: dict[str, Any],
    client_message_id: str,
    *,
    nonce: str,
    sim_slot: int | None = None,
    metadata: dict[str, Any] | None = None,
    sender: str = "Service Relay",
    body: str = "Your one-time access code is 482731",
) -> dict[str, Any]:
    payload = relay_payload(client_message_id, body=body, source="root_module")
    payload["sender"] = sender
    if sim_slot is not None:
        payload["sim_slot"] = sim_slot
    payload["metadata"] = metadata or {}
    raw, headers = hmac_request(
        created["device"]["id"],
        created["credential"]["secret"],
        payload,
        nonce=nonce,
    )
    response = client.post("/api/v1/ingest/messages", content=raw, headers=headers)
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


def ingest_hmac_message(
    client: TestClient,
    created: dict[str, Any],
    client_message_id: str,
    *,
    source: str,
    nonce: str,
    received_at: datetime,
    sim_slot: int = 0,
    metadata: dict[str, Any] | None = None,
    body: str = "Your one-time access code is 482731",
) -> dict[str, Any]:
    payload = relay_payload(client_message_id, body=body, source=source)
    payload["received_at"] = received_at.isoformat()
    payload["sim_slot"] = sim_slot
    payload["metadata"] = metadata or {}
    raw, headers = hmac_request(
        created["device"]["id"],
        created["credential"]["secret"],
        payload,
        nonce=nonce,
    )
    response = client.post("/api/v1/ingest/messages", content=raw, headers=headers)
    assert response.status_code == 201
    return cast(dict[str, Any], response.json())


def test_device_runtime_metadata_reports_online_and_credential_reuse_warning(
    client: TestClient,
) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    now = datetime.now(timezone.utc)
    ingest_hmac_message(
        client,
        created,
        "runtime-instance-a",
        source="root_module",
        nonce="runtime-instance-a",
        received_at=now,
        metadata={
            "collector_instance_id": "instance-a",
            "collector_version": "1.0.12",
            "collector_mode": "root_module",
        },
    )

    first = next(
        item
        for item in client.get("/api/v1/admin/devices", headers=ADMIN_HEADERS).json()
        if item["id"] == device_id
    )
    assert first["online"] is True
    assert first["client_instance_id"] == "instance-a"
    assert first["client_version"] == "1.0.12"
    assert first["client_mode"] == "root_module"
    assert first["runtime_updated_at"]
    assert first["credential_reuse_warning"] is False

    ingest_hmac_message(
        client,
        created,
        "runtime-instance-b",
        source="root_module",
        nonce="runtime-instance-b",
        received_at=now + timedelta(seconds=1),
        metadata={
            "collector_instance_id": "instance-b",
            "collector_version": "1.0.12",
            "collector_mode": "root_module",
        },
    )

    second = next(
        item
        for item in client.get("/api/v1/admin/devices", headers=ADMIN_HEADERS).json()
        if item["id"] == device_id
    )
    assert second["client_instance_id"] == "instance-b"
    assert second["credential_reuse_warning"] is True
    with store.session_factory() as session:
        model = session.get(DeviceModel, device_id)
        assert model is not None
        assert model.last_source_fingerprint
        assert "testclient" not in model.last_source_fingerprint


def test_duplicate_collection_warning_marks_matching_android_and_root_messages(
    client: TestClient,
) -> None:
    root = create_device(client, client_type="root_module", auth_kind="hmac")
    android = create_device(client, client_type="android_app", auth_kind="hmac")
    received_at = datetime.now(timezone.utc)
    shared_body = "Bank notification code 927415"

    ingest_hmac_message(
        client,
        root,
        "duplicate-root",
        source="root_module",
        nonce="duplicate-root",
        received_at=received_at,
        metadata={"collector_instance_id": "root-instance"},
        body=shared_body,
    )
    ingest_hmac_message(
        client,
        android,
        "duplicate-android",
        source="android_app",
        nonce="duplicate-android",
        received_at=received_at,
        metadata={"collector_instance_id": "android-instance"},
        body=shared_body,
    )

    devices = {
        item["id"]: item
        for item in client.get("/api/v1/admin/devices", headers=ADMIN_HEADERS).json()
    }
    assert devices[root["device"]["id"]]["duplicate_collection_warning"] is True
    assert devices[android["device"]["id"]]["duplicate_collection_warning"] is True


def put_sim_profile(
    client: TestClient,
    device_id: str,
    slot: int,
    *,
    subscription_id: int | None,
    phone_number: str,
    source: str = "manual",
) -> None:
    response = client.put(
        f"/api/v1/admin/devices/{device_id}/sim-profiles/{slot}",
        json={
            "subscription_id": subscription_id,
            "phone_number": phone_number,
            "source": source,
        },
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 200


def test_ingest_resolves_phone_by_subscription_before_slot(client: TestClient) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    put_sim_profile(client, device_id, 0, subscription_id=5, phone_number="+15550100005")
    put_sim_profile(client, device_id, 1, subscription_id=6, phone_number="+15550100006")

    ingest_root_message(
        client,
        created,
        "root-subscription-5",
        nonce="root-subscription-priority",
        sim_slot=1,
        metadata={"subscription_id": 5},
    )

    messages = client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json()
    message = next(item for item in messages if item["client_message_id"] == "root-subscription-5")
    assert message["receiving_phone_masked"].endswith("0005")
    assert message["receiving_identity_status"] == "resolved"
    assert message["subscription_id"] == 5
    assert "receiving_phone" not in message


def test_manual_sim_binding_reconciles_existing_messages_and_claims_by_phone(
    client: TestClient,
) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]

    ingest_root_message(
        client,
        created,
        "root-manual-reconcile",
        nonce="root-manual-reconcile",
        sim_slot=1,
        metadata={"subscription_id": 6},
    )
    before = client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json()
    message_before = next(item for item in before if item["client_message_id"] == "root-manual-reconcile")
    assert message_before["receiving_phone_masked"] is None
    assert message_before["receiving_identity_status"] == "unresolved"

    put_sim_profile(client, device_id, 1, subscription_id=6, phone_number="+15550100006")

    after = client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json()
    message_after = next(item for item in after if item["client_message_id"] == "root-manual-reconcile")
    assert message_after["receiving_phone_masked"].endswith("0006")
    assert message_after["receiving_identity_status"] == "resolved"

    api_token = create_claim_client(client)
    claimed = client.post(
        "/api/v1/otp/claim",
        json={"phone_number": "+15550100006"},
        headers={"Authorization": f"Bearer {api_token}"},
    )
    assert claimed.status_code == 200
    assert claimed.json()["code"] == "482731"


def test_ingest_resolves_phone_by_slot_when_subscription_missing(client: TestClient) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    put_sim_profile(client, device_id, 1, subscription_id=6, phone_number="+15550100006")

    ingest_root_message(
        client,
        created,
        "root-slot-1",
        nonce="root-slot-fallback",
        sim_slot=1,
    )

    message = client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json()[0]
    assert message["receiving_phone_masked"].endswith("0006")
    assert message["receiving_identity_status"] == "resolved"


def test_ingest_resolves_single_saved_device_phone_without_sim_hints(client: TestClient) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    put_sim_profile(client, device_id, 0, subscription_id=None, phone_number="+15550100007")

    ingest_root_message(
        client,
        created,
        "root-device-phone-only",
        nonce="root-device-phone-only",
        metadata={"subscription_id": 6},
    )

    message = client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json()[0]
    assert message["receiving_phone_masked"].endswith("0007")
    assert message["receiving_identity_status"] == "resolved"


def test_conflicting_sim_profiles_mark_message_unresolved(client: TestClient) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    put_sim_profile(client, device_id, 0, subscription_id=5, phone_number="+15550100005")
    put_sim_profile(client, device_id, 1, subscription_id=5, phone_number="+15550100055")

    ingest_root_message(
        client,
        created,
        "root-conflict",
        nonce="root-conflict",
        sim_slot=0,
        metadata={"subscription_id": 5},
    )

    message = client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json()[0]
    assert message["receiving_phone_masked"] is None
    assert message["receiving_identity_status"] == "conflict"


def test_auto_phone_candidate_never_overwrites_manual_profile(client: TestClient) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    put_sim_profile(client, device_id, 0, subscription_id=5, phone_number="+15550100005")

    ingest_root_message(
        client,
        created,
        "root-auto-candidate",
        nonce="root-auto-candidate",
        sim_slot=0,
        metadata={"subscription_id": 5, "phone_number": "+15550100999"},
    )

    profile = client.get(
        f"/api/v1/admin/devices/{device_id}/sim-profiles/0",
        headers=ADMIN_HEADERS,
    ).json()
    message = client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json()[0]
    assert profile["source"] == "manual"
    assert profile["phone_number_masked"].endswith("0005")
    assert message["receiving_phone_masked"].endswith("0005")


def test_ingest_tracks_both_observed_sim_slots_without_phone_numbers(client: TestClient) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]

    ingest_root_message(
        client,
        created,
        "root-observed-slot-0",
        nonce="root-observed-slot-0",
        sim_slot=0,
        metadata={"subscription_id": 5, "carrier_name": "Carrier A"},
    )
    ingest_root_message(
        client,
        created,
        "root-observed-slot-1",
        nonce="root-observed-slot-1",
        sim_slot=1,
        metadata={"subscription_id": 6, "carrier_name": "Carrier B"},
    )

    profiles = client.get(
        f"/api/v1/admin/devices/{device_id}/sim-profiles",
        headers=ADMIN_HEADERS,
    ).json()
    assert [profile["slot_index"] for profile in profiles] == [0, 1]
    assert [profile["subscription_id"] for profile in profiles] == [5, 6]
    assert [profile["phone_number_masked"] for profile in profiles] == [None, None]
    assert [profile["source"] for profile in profiles] == ["auto", "auto"]


def test_sim_profile_listing_repairs_observed_slots_from_existing_messages(
    client: TestClient,
) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    ingest_root_message(
        client,
        created,
        "root-history-slot-0",
        nonce="root-history-slot-0",
        sim_slot=0,
        metadata={"subscription_id": 5},
    )
    ingest_root_message(
        client,
        created,
        "root-history-slot-1",
        nonce="root-history-slot-1",
        sim_slot=1,
        metadata={"subscription_id": 6},
    )
    with store.session_factory.begin() as session:
        session.query(DeviceSimProfileModel).filter(
            DeviceSimProfileModel.device_id == device_id
        ).delete()

    profiles = client.get(
        f"/api/v1/admin/devices/{device_id}/sim-profiles",
        headers=ADMIN_HEADERS,
    ).json()

    assert [(profile["slot_index"], profile["subscription_id"]) for profile in profiles] == [
        (0, 5),
        (1, 6),
    ]


def test_admin_message_filters_by_receiving_phone_sender_keyword_and_slot(
    client: TestClient,
) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    put_sim_profile(client, device_id, 0, subscription_id=5, phone_number="+15550100005")
    put_sim_profile(client, device_id, 1, subscription_id=6, phone_number="+15550100006")
    ingest_root_message(
        client,
        created,
        "filter-target",
        nonce="filter-target",
        sim_slot=0,
        metadata={"subscription_id": 5},
        sender="Target Service",
        body="Target keyword verification code 482731",
    )
    ingest_root_message(
        client,
        created,
        "filter-other",
        nonce="filter-other",
        sim_slot=1,
        metadata={"subscription_id": 6},
        sender="Other Service",
        body="Unrelated message 927415",
    )

    response = client.get(
        "/api/v1/admin/messages",
        params={
            "phone_number": "+15550100005",
            "device_id": device_id,
            "sim_slot": 0,
            "sender": "Target Service",
            "keyword": "verification",
            "identity_status": "resolved",
            "otp_status": "available",
            "read": "false",
            "limit": 20,
            "offset": 0,
        },
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 200
    assert [item["client_message_id"] for item in response.json()] == ["filter-target"]


def test_admin_message_filter_by_bound_iphone_sim_matches_shortcut_message_without_slot(
    client: TestClient,
) -> None:
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    device_id = created["device"]["id"]
    token = created["credential"]["secret"]
    put_sim_profile(client, device_id, 0, subscription_id=None, phone_number="+15550100005")

    ingested = client.post(
        "/api/v1/ingest/shortcut",
        json={"message": {"Sender": "iPhone Messages", "Content": "Your code is 482731"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert ingested.status_code == 201

    filtered = client.get(
        "/api/v1/admin/messages",
        params={"device_id": device_id, "sim_slot": 0},
        headers=ADMIN_HEADERS,
    )

    assert filtered.status_code == 200
    assert len(filtered.json()) == 1
    assert filtered.json()[0]["device_id"] == device_id
    assert filtered.json()[0]["receiving_phone_masked"].endswith("0005")


def test_admin_can_delete_message_and_its_otp(client: TestClient) -> None:
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    token = created["credential"]["secret"]
    ingested = client.post(
        "/api/v1/ingest/shortcut",
        json={"message": "Your one-time access code is 482731"},
        headers={"Authorization": f"Bearer {token}"},
    )
    message_id = ingested.json()["message_id"]

    deleted = client.delete(f"/api/v1/admin/messages/{message_id}", headers=ADMIN_HEADERS)

    assert deleted.status_code == 204
    assert client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json() == []
    assert client.get("/api/v1/admin/otps", headers=ADMIN_HEADERS).json() == []
    missing = client.delete(f"/api/v1/admin/messages/{message_id}", headers=ADMIN_HEADERS)
    assert missing.status_code == 404


def test_admin_can_bulk_delete_messages_and_ignore_unknown_ids(client: TestClient) -> None:
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    token = created["credential"]["secret"]
    first = client.post(
        "/api/v1/ingest/shortcut",
        json={"message": {"Content": "Your code is 111111", "Date": "2026-08-22T01:00:00Z"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    second = client.post(
        "/api/v1/ingest/shortcut",
        json={"message": {"Content": "Your code is 222222", "Date": "2026-08-22T01:01:00Z"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    first_id = first.json()["message_id"]
    second_id = second.json()["message_id"]

    deleted = client.post(
        "/api/v1/admin/messages/bulk-delete",
        json={"message_ids": [first_id, second_id, str(UUID(int=0))]},
        headers=ADMIN_HEADERS,
    )

    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": 2}
    assert client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json() == []
    invalid = client.post(
        "/api/v1/admin/messages/bulk-delete",
        json={"message_ids": []},
        headers=ADMIN_HEADERS,
    )
    assert invalid.status_code == 422


def test_otp_claim_filters_by_receiving_phone_and_text_endpoint_returns_code(
    client: TestClient,
) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    put_sim_profile(client, device_id, 0, subscription_id=5, phone_number="+15550100005")
    put_sim_profile(client, device_id, 1, subscription_id=6, phone_number="+15550100006")
    ingest_root_message(
        client,
        created,
        "claim-phone-target",
        nonce="claim-phone-target",
        sim_slot=0,
        metadata={"subscription_id": 5},
        sender="Target Service",
    )
    ingest_root_message(
        client,
        created,
        "claim-phone-other",
        nonce="claim-phone-other",
        sim_slot=1,
        metadata={"subscription_id": 6},
        body="Your one-time access code is 927415",
    )
    api_token = create_claim_client(client)
    claim_headers = {"Authorization": f"Bearer {api_token}"}

    claimed = client.get(
        "/api/v1/otp/claim.txt",
        params={"phone_number": "+15550100005", "sender": "Target Service"},
        headers=claim_headers,
    )

    assert claimed.status_code == 200
    assert claimed.headers["content-type"].startswith("text/plain")
    assert claimed.text == "482731"
    assert client.post(
        "/api/v1/otp/claim",
        json={"phone_number": "+15550100005"},
        headers=claim_headers,
    ).status_code == 404


def test_message_read_state_is_separate_from_otp_claim_state(client: TestClient) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    put_sim_profile(client, device_id, 0, subscription_id=5, phone_number="+15550100005")
    ingested = ingest_root_message(
        client,
        created,
        "read-state",
        nonce="read-state",
        sim_slot=0,
        metadata={"subscription_id": 5},
    )
    message_id = ingested["message_id"]

    marked = client.post(
        f"/api/v1/admin/messages/{message_id}/read",
        headers=ADMIN_HEADERS,
    )
    assert marked.status_code == 200
    assert marked.json()["read_at"] is not None
    # 单条变更接口必须和列表接口一样带上可用验证码，否则管理端标记已读后按钮会消失。
    assert marked.json()["otp_code"] == "482731"
    listed = client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json()[0]
    assert listed["otp_status"] == "available"
    assert listed["otp_code"] == "482731"
    assert listed["read_at"] is not None

    api_token = create_claim_client(client)
    assert client.post(
        "/api/v1/otp/claim",
        json={"phone_number": "+15550100005"},
        headers={"Authorization": f"Bearer {api_token}"},
    ).status_code == 200
    listed_after_claim = client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json()[0]
    assert listed_after_claim["otp_status"] == "claimed"
    # 已领取后与 WS 路径一致：不再回吐验证码，只保留状态供前端解释原因。
    assert listed_after_claim["otp_code"] is None
    assert listed_after_claim["read_at"] is not None

    unread = client.post(
        f"/api/v1/admin/messages/{message_id}/unread",
        headers=ADMIN_HEADERS,
    )
    assert unread.status_code == 200
    assert unread.json()["read_at"] is None
    assert unread.json()["otp_status"] == "claimed"
    assert unread.json()["otp_code"] is None


def test_list_messages_does_not_expose_expired_otp_code(client: TestClient) -> None:
    """过期验证码与 WS 交付路径同规则：列表接口不回吐明文，也不可再领取。"""
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    put_sim_profile(client, device_id, 0, subscription_id=5, phone_number="+15550100005")
    message_id = ingest_root_message(
        client,
        created,
        "expired-otp",
        nonce="expired-otp",
        sim_slot=0,
        metadata={"subscription_id": 5},
    )["message_id"]

    with store.session_factory.begin() as session:
        session.execute(
            update(OtpModel)
            .where(OtpModel.message_id == message_id)
            .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )

    listed = client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json()[0]
    assert listed["otp_code"] is None

    api_token = create_claim_client(client)
    assert client.post(
        "/api/v1/otp/claim",
        json={"phone_number": "+15550100005"},
        headers={"Authorization": f"Bearer {api_token}"},
    ).status_code == 404


def _rewrite_profile_phone_ciphertext(device_id: str, slot: int, raw_phone: str) -> None:
    """把绑定号码写成未规范化的裸格式，模拟 E.164 统一之前留下的存量行。"""
    with store.session_factory.begin() as session:
        profile = session.scalar(
            select(DeviceSimProfileModel).where(
                DeviceSimProfileModel.device_id == device_id,
                DeviceSimProfileModel.slot_index == slot,
            )
        )
        assert profile is not None
        encrypted = store.crypto.encrypt_text(
            raw_phone,
            f"device-sim-profile:{device_id}:{slot}:phone:v1",
        )
        profile.phone_number_nonce = encrypted.nonce
        profile.phone_number_ciphertext = encrypted.ciphertext
        profile.phone_number_key_version = encrypted.key_version


def test_legacy_unnormalized_sim_binding_still_routes_claim_by_phone(
    client: TestClient,
) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    put_sim_profile(client, device_id, 1, subscription_id=6, phone_number="13800138001")
    _rewrite_profile_phone_ciphertext(device_id, 1, "13800138001")

    ingest_root_message(
        client,
        created,
        "root-legacy-bare-phone",
        nonce="root-legacy-bare-phone",
        sim_slot=1,
        metadata={"subscription_id": 6},
    )

    api_token = create_claim_client(client)
    claimed = client.post(
        "/api/v1/otp/claim",
        json={"phone_number": "13800138001"},
        headers={"Authorization": f"Bearer {api_token}"},
    )
    assert claimed.status_code == 200
    assert claimed.json()["code"] == "482731"


def test_legacy_unnormalized_sim_binding_is_reported_in_e164_form(
    client: TestClient,
) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    put_sim_profile(client, device_id, 0, subscription_id=None, phone_number="13800138000")
    _rewrite_profile_phone_ciphertext(device_id, 0, "13800138000")

    ingest_root_message(
        client,
        created,
        "root-legacy-bare-phone-e164",
        nonce="root-legacy-bare-phone-e164",
        sim_slot=0,
    )

    listed = client.get(
        "/api/v1/admin/messages",
        params={"phone_number": "+8613800138000"},
        headers=ADMIN_HEADERS,
    )
    assert listed.status_code == 200
    assert [item["client_message_id"] for item in listed.json()] == [
        "root-legacy-bare-phone-e164"
    ]


def test_same_phone_on_two_devices_routes_independently(client: TestClient) -> None:
    """同一张卡同时绑在 Root 与 App 两台设备上时，路由必须各自独立。"""
    root = create_device(client, client_type="root_module", auth_kind="hmac")
    app = create_device(client, client_type="android_app", auth_kind="hmac")
    put_sim_profile(
        client, root["device"]["id"], 0, subscription_id=3, phone_number="13800138002"
    )
    put_sim_profile(
        client, app["device"]["id"], 0, subscription_id=None, phone_number="13800138002"
    )

    ingest_root_message(
        client, root, "same-phone-root", nonce="same-phone-root", sim_slot=0
    )
    app_payload = relay_payload("same-phone-app", source="android_app")
    app_payload["sim_slot"] = 0
    app_payload["metadata"] = {}
    app_raw, app_headers = hmac_request(
        app["device"]["id"],
        app["credential"]["secret"],
        app_payload,
        nonce="same-phone-app",
    )
    assert client.post(
        "/api/v1/ingest/messages", content=app_raw, headers=app_headers
    ).status_code == 201

    listed = client.get(
        "/api/v1/admin/messages",
        params={"phone_number": "13800138002"},
        headers=ADMIN_HEADERS,
    ).json()
    by_id = {item["client_message_id"]: item for item in listed}
    assert set(by_id) == {"same-phone-root", "same-phone-app"}
    assert by_id["same-phone-root"]["receiving_identity_status"] == "resolved"
    assert by_id["same-phone-app"]["receiving_identity_status"] == "resolved"
    assert by_id["same-phone-root"]["device_id"] != by_id["same-phone-app"]["device_id"]
