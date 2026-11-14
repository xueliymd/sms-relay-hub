"""远程控制 v1：管理端下发指令 / 设备轮询执行 / 结果回传。

覆盖：指令入队与白名单、设备轮询的 HMAC 鉴权与设备一致性、幂等的 pending 取出、
结果加密落库与解密返回、跨设备篡改拒绝、结果体积上限、撤销设备后拒绝。
"""

import base64
import hashlib
import hmac
import json
import time
from datetime import timedelta
from typing import Any, cast

from fastapi.testclient import TestClient
from sqlalchemy import update

from app.core.config import settings
from app.db.models import DeviceCommandModel
from app.services.store import store, utc_now
from tests.test_relay import ADMIN_HEADERS, create_device


def signed_headers(device_id: str, secret: str, raw: bytes, *, nonce: str) -> dict[str, str]:
    """按平台规范对任意 body（可为空）签名，用于命令轮询与结果回传。"""
    timestamp = str(int(time.time()))
    key = base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))
    canonical = f"{timestamp}\n{nonce}\n{hashlib.sha256(raw).hexdigest()}".encode()
    signature = (
        base64.urlsafe_b64encode(hmac.new(key, canonical, hashlib.sha256).digest())
        .decode()
        .rstrip("=")
    )
    return {
        "Content-Type": "application/json",
        "X-Device-Id": device_id,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
    }


def create_hmac_device(client: TestClient) -> tuple[str, str]:
    created = create_device(client, client_type="android_app", auth_kind="hmac")
    return cast(str, created["device"]["id"]), cast(str, created["credential"]["secret"])


def enqueue_command(client: TestClient, device_id: str, command_type: str, nonce: str) -> Any:
    return client.post(
        f"/api/v1/admin/devices/{device_id}/commands",
        json={"command_type": command_type},
        headers={**ADMIN_HEADERS, "X-Nonce": nonce},
    )


def test_command_lifecycle_issue_poll_execute_and_read_back(client: TestClient) -> None:
    device_id, secret = create_hmac_device(client)

    issued = enqueue_command(client, device_id, "query_battery", "cmd-issue-1")
    assert issued.status_code == 201
    assert issued.json()["status"] == "pending"
    command_id = issued.json()["id"]

    # 设备轮询：拿到指令并置为 sent，第二次轮询不再重复下发
    first_poll = client.get(
        f"/api/v1/devices/{device_id}/commands/pending",
        headers=signed_headers(device_id, secret, b"", nonce="cmd-poll-1"),
    )
    assert first_poll.status_code == 200
    commands = first_poll.json()["commands"]
    assert [c["id"] for c in commands] == [command_id]
    assert commands[0]["command_type"] == "query_battery"
    assert commands[0]["result"] is None

    second_poll = client.get(
        f"/api/v1/devices/{device_id}/commands/pending",
        headers=signed_headers(device_id, secret, b"", nonce="cmd-poll-2"),
    )
    assert second_poll.status_code == 200
    assert second_poll.json()["commands"] == []

    # 设备回传结果
    result_body = json.dumps(
        {"result": {"battery_percent": 87, "charging": True}},
        separators=(",", ":"),
    ).encode()
    posted = client.post(
        f"/api/v1/devices/{device_id}/commands/{command_id}/result",
        content=result_body,
        headers=signed_headers(device_id, secret, result_body, nonce="cmd-result-1"),
    )
    assert posted.status_code == 200
    assert posted.json() == {"accepted": True, "status": "completed"}

    listed = client.get(
        f"/api/v1/admin/devices/{device_id}/commands",
        headers={**ADMIN_HEADERS, "X-Nonce": "cmd-list-1"},
    )
    assert listed.status_code == 200
    entries = listed.json()
    assert len(entries) == 1
    assert entries[0]["status"] == "completed"
    assert entries[0]["result"] == {"battery_percent": 87, "charging": True}
    assert entries[0]["sent_at"] is not None
    assert entries[0]["completed_at"] is not None


def test_command_result_is_idempotent_for_the_same_device(client: TestClient) -> None:
    device_id, secret = create_hmac_device(client)
    command_id = enqueue_command(client, device_id, "trigger_upload", "cmd-dup-issue").json()["id"]
    client.get(
        f"/api/v1/devices/{device_id}/commands/pending",
        headers=signed_headers(device_id, secret, b"", nonce="cmd-dup-poll"),
    )
    body = json.dumps({"result": {"queued": 3}}, separators=(",", ":")).encode()
    first = client.post(
        f"/api/v1/devices/{device_id}/commands/{command_id}/result",
        content=body,
        headers=signed_headers(device_id, secret, body, nonce="cmd-dup-r1"),
    )
    second = client.post(
        f"/api/v1/devices/{device_id}/commands/{command_id}/result",
        content=body,
        headers=signed_headers(device_id, secret, body, nonce="cmd-dup-r2"),
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "completed"


def test_command_endpoints_reject_invalid_signature_and_device_mismatch(client: TestClient) -> None:
    device_id, secret = create_hmac_device(client)
    other_id, other_secret = create_hmac_device(client)
    enqueue_command(client, device_id, "query_battery", "cmd-auth-issue")

    # 签名无效
    broken = signed_headers(device_id, secret, b"", nonce="cmd-auth-1")
    broken["X-Signature"] = "AAAA"
    assert (
        client.get(f"/api/v1/devices/{device_id}/commands/pending", headers=broken).status_code
        == 401
    )

    # 缺鉴权头
    assert client.get(f"/api/v1/devices/{device_id}/commands/pending").status_code == 401

    # 凭据与路径设备不一致（用另一台设备的合法签名访问本设备路径）
    mismatch = client.get(
        f"/api/v1/devices/{device_id}/commands/pending",
        headers=signed_headers(other_id, other_secret, b"", nonce="cmd-auth-2"),
    )
    assert mismatch.status_code == 403
    assert mismatch.json()["error"]["code"] == "device_mismatch"


def test_command_result_rejects_unknown_command_and_oversized_payload(client: TestClient) -> None:
    device_id, secret = create_hmac_device(client)
    body = json.dumps({"result": {"ok": True}}, separators=(",", ":")).encode()
    missing = client.post(
        f"/api/v1/devices/{device_id}/commands/00000000-0000-0000-0000-000000000000/result",
        content=body,
        headers=signed_headers(device_id, secret, body, nonce="cmd-miss-1"),
    )
    assert missing.status_code == 404

    oversized_result = {"blob": "x" * 20000}
    oversized_body = json.dumps({"result": oversized_result}, separators=(",", ":")).encode()
    oversized = client.post(
        f"/api/v1/devices/{device_id}/commands/00000000-0000-0000-0000-000000000000/result",
        content=oversized_body,
        headers=signed_headers(device_id, secret, oversized_body, nonce="cmd-miss-2"),
    )
    assert oversized.status_code == 422


def test_command_rejects_unsupported_type_and_revoked_device(client: TestClient) -> None:
    device_id, secret = create_hmac_device(client)

    unsupported = enqueue_command(client, device_id, "wipe_everything", "cmd-bad-1")
    assert unsupported.status_code == 422
    assert unsupported.json()["error"]["code"] == "invalid_command"

    # 撤销后：管理端不能再下发，设备也不能再轮询
    assert (
        client.delete(
            f"/api/v1/admin/devices/{device_id}", headers={**ADMIN_HEADERS, "X-Nonce": "cmd-rev-1"}
        ).status_code
        == 204
    )
    revoked_issue = enqueue_command(client, device_id, "query_battery", "cmd-rev-2")
    assert revoked_issue.status_code == 409
    revoked_poll = client.get(
        f"/api/v1/devices/{device_id}/commands/pending",
        headers=signed_headers(device_id, secret, b"", nonce="cmd-rev-3"),
    )
    assert revoked_poll.status_code == 401


def test_command_admin_endpoints_require_admin_token(client: TestClient) -> None:
    device_id, _ = create_hmac_device(client)
    assert (
        client.post(
            f"/api/v1/admin/devices/{device_id}/commands", json={"command_type": "query_battery"}
        ).status_code
        == 401
    )
    assert client.get(f"/api/v1/admin/devices/{device_id}/commands").status_code == 401


def test_command_queue_is_capped_per_device(client: TestClient) -> None:
    device_id, _ = create_hmac_device(client)
    for index in range(20):
        response = enqueue_command(client, device_id, "query_battery", f"cmd-cap-{index}")
        assert response.status_code == 201
    overflow = enqueue_command(client, device_id, "query_battery", "cmd-cap-overflow")
    assert overflow.status_code == 429
    assert overflow.json()["error"]["code"] == "command_queue_full"


def test_stale_sent_command_is_redelivered_after_timeout(client: TestClient) -> None:
    """设备取走指令后进程被杀（无结果）→ 超时后应重新投递，避免指令永久卡住。"""
    device_id, secret = create_hmac_device(client)
    command_id = enqueue_command(client, device_id, "query_battery", "cmd-stale-issue").json()["id"]
    first_poll = client.get(
        f"/api/v1/devices/{device_id}/commands/pending",
        headers=signed_headers(device_id, secret, b"", nonce="cmd-stale-poll-1"),
    )
    assert [c["id"] for c in first_poll.json()["commands"]] == [command_id]

    # 尚未超时：不重投
    immediate = client.get(
        f"/api/v1/devices/{device_id}/commands/pending",
        headers=signed_headers(device_id, secret, b"", nonce="cmd-stale-poll-2"),
    )
    assert immediate.json()["commands"] == []

    # 把 sent_at 拨回到超时之前，模拟设备取走后没有回传结果
    with store.session_factory.begin() as session:
        session.execute(
            update(DeviceCommandModel)
            .where(DeviceCommandModel.id == command_id)
            .values(sent_at=utc_now() - timedelta(minutes=11))
        )

    redelivered = client.get(
        f"/api/v1/devices/{device_id}/commands/pending",
        headers=signed_headers(device_id, secret, b"", nonce="cmd-stale-poll-3"),
    )
    assert [c["id"] for c in redelivered.json()["commands"]] == [command_id]


def test_unclaimed_command_expires_instead_of_waiting_forever(client: TestClient) -> None:
    """软件未对接/长期不轮询时，pending 必须有明确终点而不是永久等待。"""
    device_id, secret = create_hmac_device(client)
    command_id = enqueue_command(client, device_id, "query_battery", "cmd-expire-issue").json()[
        "id"
    ]

    with store.session_factory.begin() as session:
        session.execute(
            update(DeviceCommandModel)
            .where(DeviceCommandModel.id == command_id)
            .values(
                created_at=utc_now()
                - timedelta(seconds=settings.device_command_pending_ttl_seconds + 1)
            )
        )

    listed = client.get(
        f"/api/v1/admin/devices/{device_id}/commands",
        headers={**ADMIN_HEADERS, "X-Nonce": "cmd-expire-list"},
    )
    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "expired"
    assert listed.json()[0]["completed_at"] is not None

    polled = client.get(
        f"/api/v1/devices/{device_id}/commands/pending",
        headers=signed_headers(device_id, secret, b"", nonce="cmd-expire-poll"),
    )
    assert polled.status_code == 200
    assert polled.json()["commands"] == []


def test_sent_command_expires_from_claim_time_when_result_never_arrives(client: TestClient) -> None:
    """已领取指令按 sent_at 计算 30 分钟回传窗口，同时仍受总生命周期硬上限约束。"""
    device_id, secret = create_hmac_device(client)
    command_id = enqueue_command(
        client, device_id, "trigger_upload", "cmd-sent-expire-issue"
    ).json()["id"]
    client.get(
        f"/api/v1/devices/{device_id}/commands/pending",
        headers=signed_headers(device_id, secret, b"", nonce="cmd-sent-expire-poll"),
    )

    now = utc_now()
    with store.session_factory.begin() as session:
        session.execute(
            update(DeviceCommandModel)
            .where(DeviceCommandModel.id == command_id)
            .values(
                created_at=now - timedelta(minutes=36),
                sent_at=now - timedelta(minutes=26),
            )
        )

    still_waiting = client.get(
        f"/api/v1/admin/devices/{device_id}/commands",
        headers={**ADMIN_HEADERS, "X-Nonce": "cmd-sent-still-waiting"},
    ).json()
    assert still_waiting[0]["status"] == "sent", "尚未满领取后的 30 分钟，不应提前过期"

    with store.session_factory.begin() as session:
        session.execute(
            update(DeviceCommandModel)
            .where(DeviceCommandModel.id == command_id)
            .values(
                sent_at=utc_now()
                - timedelta(seconds=settings.device_command_result_ttl_seconds + 1)
            )
        )

    listed = client.get(
        f"/api/v1/admin/devices/{device_id}/commands",
        headers={**ADMIN_HEADERS, "X-Nonce": "cmd-sent-expire-list"},
    ).json()
    assert listed[0]["status"] == "expired"

    late_body = json.dumps({"result": {"queued": 1}}, separators=(",", ":")).encode()
    late_result = client.post(
        f"/api/v1/devices/{device_id}/commands/{command_id}/result",
        content=late_body,
        headers=signed_headers(device_id, secret, late_body, nonce="cmd-sent-expire-result"),
    )
    assert late_result.status_code == 409
    assert late_result.json()["error"]["code"] == "command_expired"


def test_sent_command_cannot_be_extended_forever_by_redelivery(client: TestClient) -> None:
    """反复重投也不能无限续期：创建时间超过 pending + result 后必须收口。"""
    device_id, secret = create_hmac_device(client)
    command_id = enqueue_command(client, device_id, "query_battery", "cmd-hard-cap-issue").json()[
        "id"
    ]
    client.get(
        f"/api/v1/devices/{device_id}/commands/pending",
        headers=signed_headers(device_id, secret, b"", nonce="cmd-hard-cap-poll"),
    )

    now = utc_now()
    with store.session_factory.begin() as session:
        session.execute(
            update(DeviceCommandModel)
            .where(DeviceCommandModel.id == command_id)
            .values(
                created_at=now
                - timedelta(
                    seconds=settings.device_command_pending_ttl_seconds
                    + settings.device_command_result_ttl_seconds
                    + 1
                ),
                sent_at=now,
            )
        )

    listed = client.get(
        f"/api/v1/admin/devices/{device_id}/commands",
        headers={**ADMIN_HEADERS, "X-Nonce": "cmd-hard-cap-list"},
    ).json()
    assert listed[0]["status"] == "expired"


def test_command_enqueue_rejects_clients_without_persistent_poller(client: TestClient) -> None:
    """iOS Ve/HMAC 也不能接收下行指令，否则只会等到过期。"""
    for index, client_type in enumerate(("ve_tweak", "air724_mqtt")):
        created = create_device(client, client_type=client_type, auth_kind="hmac")
        device_id = cast(str, created["device"]["id"])
        response = enqueue_command(
            client,
            device_id,
            "query_battery",
            f"cmd-unsupported-{index}",
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "command_not_supported"


def test_command_list_rejects_unknown_device(client: TestClient) -> None:
    missing = client.get(
        "/api/v1/admin/devices/00000000-0000-0000-0000-000000000000/commands",
        headers=ADMIN_HEADERS,
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "device_not_found"


def test_recent_sms_result_stays_encrypted_at_rest(client: TestClient) -> None:
    """结果以密文入库：直接读表拿不到明文，只能经管理端 API 解密返回。"""
    device_id, secret = create_hmac_device(client)
    command_id = enqueue_command(client, device_id, "query_recent_sms", "cmd-enc-issue").json()[
        "id"
    ]
    client.get(
        f"/api/v1/devices/{device_id}/commands/pending",
        headers=signed_headers(device_id, secret, b"", nonce="cmd-enc-poll"),
    )
    body = json.dumps(
        {"result": {"count": 1, "messages": [{"sender": "****1234", "body": "验证码 9999"}]}},
        separators=(",", ":"),
    ).encode()
    assert (
        client.post(
            f"/api/v1/devices/{device_id}/commands/{command_id}/result",
            content=body,
            headers=signed_headers(device_id, secret, body, nonce="cmd-enc-result"),
        ).status_code
        == 200
    )

    with store.session_factory() as session:
        model = session.get(DeviceCommandModel, command_id)
        assert model is not None
        assert model.status == "completed"
        assert model.result_ciphertext
        assert b"9999" not in bytes(model.result_ciphertext)

    listed = client.get(
        f"/api/v1/admin/devices/{device_id}/commands",
        headers={**ADMIN_HEADERS, "X-Nonce": "cmd-enc-list"},
    ).json()
    assert listed[0]["result"]["messages"][0]["body"] == "验证码 9999"
