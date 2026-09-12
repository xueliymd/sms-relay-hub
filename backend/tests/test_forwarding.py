"""转发规则引擎测试：CRUD/鉴权/匹配/模板/渠道校验/入库分发/日志。"""

import asyncio
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import relay as relay_api
from app.api.relay import build_shortcut_payload  # noqa: F401 - 确认模块可导入
from app.services import forwarding as forwarding_module
from tests.test_relay import ADMIN_HEADERS, create_device


def _auth(client: TestClient) -> dict[str, Any]:
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    return {"Authorization": f"Bearer {created['credential']['secret']}"}


def _rule_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "验证码转发",
        "enabled": True,
        "channel": "dingtalk",
        "target": "https://oapi.dingtalk.com/robot/send?access_token=abc123",
        "match_sender": "10086",
        "match_keyword": "验证码,code",
    }
    payload.update(overrides)
    return payload


# ---------- CRUD 与鉴权 ----------


def test_forwarding_rule_crud_requires_admin(client: TestClient) -> None:
    assert client.get("/api/v1/admin/forwarding/rules").status_code == 401
    assert (
        client.post("/api/v1/admin/forwarding/rules", json=_rule_payload()).status_code == 401
    )
    assert (
        client.delete("/api/v1/admin/forwarding/rules/does-not-exist").status_code == 401
    )


def test_forwarding_rule_crud_lifecycle(client: TestClient) -> None:
    created = client.post(
        "/api/v1/admin/forwarding/rules", json=_rule_payload(), headers=ADMIN_HEADERS
    )
    assert created.status_code == 201
    rule = created.json()["rule"]
    assert rule["name"] == "验证码转发"
    assert rule["channel"] == "dingtalk"
    assert rule["target_masked"] == "https://oapi.dingtalk.com/***"

    listed = client.get("/api/v1/admin/forwarding/rules", headers=ADMIN_HEADERS).json()
    assert [item["id"] for item in listed["rules"]] == [rule["id"]]
    assert listed["channels"][0]["id"] in {"wecom", "dingtalk", "feishu", "bark", "webhook"}

    updated = client.put(
        f"/api/v1/admin/forwarding/rules/{rule['id']}",
        json={"name": "改名", "enabled": False, "match_keyword": "新关键词"},
        headers=ADMIN_HEADERS,
    )
    assert updated.status_code == 200
    body = updated.json()["rule"]
    assert body["name"] == "改名" and body["enabled"] is False
    assert body["match_keyword"] == "新关键词"
    # 未提供 target 时保留已存地址
    assert body["target_masked"] == rule["target_masked"]

    deleted = client.delete(
        f"/api/v1/admin/forwarding/rules/{rule['id']}", headers=ADMIN_HEADERS
    )
    assert deleted.status_code == 200
    assert client.get("/api/v1/admin/forwarding/rules", headers=ADMIN_HEADERS).json()["rules"] == []


def test_forwarding_rule_validation(client: TestClient) -> None:
    # 缺名称
    assert (
        client.post(
            "/api/v1/admin/forwarding/rules",
            json={"channel": "wecom", "target": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x"},
            headers=ADMIN_HEADERS,
        ).status_code
        == 422
    )
    # 钉钉地址域名校验
    assert (
        client.post(
            "/api/v1/admin/forwarding/rules",
            json=_rule_payload(target="https://example.com/robot/send?access_token=x"),
            headers=ADMIN_HEADERS,
        ).status_code
        == 422
    )
    # 创建必须提供目标
    assert (
        client.post(
            "/api/v1/admin/forwarding/rules",
            json=_rule_payload(target=""),
            headers=ADMIN_HEADERS,
        ).status_code
        == 422
    )
    # 更新时目标留空 = 沿用已存地址（不报错）
    created = client.post("/api/v1/admin/forwarding/rules", json=_rule_payload(), headers=ADMIN_HEADERS).json()
    updated = client.put(
        f"/api/v1/admin/forwarding/rules/{created['rule']['id']}",
        json={"name": "仅改名"},
        headers=ADMIN_HEADERS,
    )
    assert updated.status_code == 200


# ---------- 匹配与模板（纯逻辑） ----------


def test_forwarding_match_logic() -> None:
    base = {
        "match_device_id": None,
        "match_phone_lookup_hash": None,
        "match_sender": None,
        "match_keyword": None,
    }
    from app.services.forwarding import match_forwarding_rule

    assert match_forwarding_rule(base, device_id="d1", receiving_phone_lookup_hash=None, sender="10086", body="验证码 482731")

    device_rule = {**base, "match_device_id": "device-a"}
    assert match_forwarding_rule(device_rule, device_id="device-a", receiving_phone_lookup_hash=None, sender="x", body="y")
    assert not match_forwarding_rule(device_rule, device_id="device-b", receiving_phone_lookup_hash=None, sender="x", body="y")

    sender_rule = {**base, "match_sender": "10086"}
    assert match_forwarding_rule(sender_rule, device_id="d", receiving_phone_lookup_hash=None, sender="10086", body="x")
    assert not match_forwarding_rule(sender_rule, device_id="d", receiving_phone_lookup_hash=None, sender="10010", body="x")

    keyword_rule = {**base, "match_keyword": "验证码, code"}
    assert match_forwarding_rule(keyword_rule, device_id="d", receiving_phone_lookup_hash=None, sender="x", body="您的 Code 是 482731")
    assert not match_forwarding_rule(keyword_rule, device_id="d", receiving_phone_lookup_hash=None, sender="x", body="无关内容")


def test_forwarding_template_render() -> None:
    from app.services.forwarding import render_forwarding_content

    variables = {"sender": "10086", "body": "验证码 482731", "phone": "+8613800138000", "code": "482731", "received_at": "2026-09-10"}
    rendered = render_forwarding_content("来自 {{sender}}：{{body}}（验证码 {{code}}）", variables)
    assert rendered == "来自 10086：验证码 482731（验证码 482731）"
    default = render_forwarding_content(None, variables)
    assert "验证码 482731" in default and "10086" in default


def test_forwarding_target_validation() -> None:
    from app.services.forwarding import validate_forwarding_target

    assert validate_forwarding_target("dingtalk", "https://oapi.dingtalk.com/robot/send?access_token=abc")
    with pytest.raises(ValueError):
        validate_forwarding_target("dingtalk", "https://example.com/robot/send?access_token=abc")
    assert validate_forwarding_target("feishu", "https://open.feishu.cn/open-apis/bot/v2/hook/abc123")
    with pytest.raises(ValueError):
        validate_forwarding_target("feishu", "https://open.feishu.cn/open-apis/bot/v2/hook/")
    assert validate_forwarding_target("bark", "https://api.day.app/barkkey")
    with pytest.raises(ValueError):
        validate_forwarding_target("bark", "https://api.day.app/")
    assert validate_forwarding_target("webhook", "http://192.168.1.8:9000/hook")
    with pytest.raises(ValueError):
        validate_forwarding_target("webhook", "ftp://example.com")


# ---------- 入库分发与日志（端到端） ----------


def test_ingest_dispatches_to_forwarding_engine(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    auth = _auth(client)
    scheduled: list[Any] = []
    monkeypatch.setattr(
        relay_api.forwarding_engine_instance,
        "schedule",
        lambda message: scheduled.append(message),
    )

    rule_payload = _rule_payload(
        channel="webhook", target="http://127.0.0.1:9/catch", match_sender=None
    )
    rule = client.post(
        "/api/v1/admin/forwarding/rules", json=rule_payload, headers=ADMIN_HEADERS
    ).json()["rule"]

    response = client.post(
        "/api/v1/ingest/shortcut", json={"message": "验证码 482731"}, headers=auth
    )
    assert response.status_code == 201
    assert len(scheduled) == 1
    message = scheduled[0]
    assert message.body == "验证码 482731"
    assert str(rule["id"])

    # 直接驱动引擎（绕过异步调度）：webhook 发送被本地拒绝（不可达），验证重试后落失败日志
    async def run_process() -> None:
        await relay_api.forwarding_engine_instance.process(message)

    asyncio.run(run_process())
    logs = client.get(
        "/api/v1/admin/forwarding/logs?limit=10", headers=ADMIN_HEADERS
    ).json()["logs"]
    assert logs, "应有转发日志"
    entry = logs[0]
    assert entry["rule_id"] == rule["id"]
    assert entry["channel"] == "webhook"
    assert entry["status"] in {"failed", "success"}


def test_ingest_dispatch_success_records_success_log(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = _auth(client)
    scheduled: list[Any] = []
    monkeypatch.setattr(
        relay_api.forwarding_engine_instance,
        "schedule",
        lambda message: scheduled.append(message),
    )
    client.post(
        "/api/v1/admin/forwarding/rules",
        json=_rule_payload(channel="webhook", target="http://127.0.0.1:9/catch", match_sender="10086"),
        headers=ADMIN_HEADERS,
    )

    def fake_send(channel: str, target: str, title: str, content: str, *, timeout: int = 10):
        assert channel == "webhook"
        return True, 200, "HTTP 200"

    monkeypatch.setattr(forwarding_module, "send_to_channel", fake_send)

    response = client.post(
        "/api/v1/ingest/shortcut",
        json={"message": {"Sender": "10086", "Content": "验证码 246810"}},
        headers=auth,
    )
    assert response.status_code == 201

    async def run_process() -> None:
        await relay_api.forwarding_engine_instance.process(scheduled[0])

    asyncio.run(run_process())
    logs = client.get(
        "/api/v1/admin/forwarding/logs?limit=10", headers=ADMIN_HEADERS
    ).json()["logs"]
    success = [log for log in logs if log["status"] == "success"]
    assert success, json.dumps(logs, ensure_ascii=False)
    assert success[0]["channel"] == "webhook"


def test_duplicate_message_is_not_forwarded_again(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = _auth(client)
    scheduled: list[Any] = []
    monkeypatch.setattr(
        relay_api.forwarding_engine_instance,
        "schedule",
        lambda message: scheduled.append(message),
    )
    client.post(
        "/api/v1/admin/forwarding/rules", json=_rule_payload(), headers=ADMIN_HEADERS
    )
    body = {"message": "验证码 135711"}
    first = client.post("/api/v1/ingest/shortcut", json=body, headers=auth)
    second = client.post("/api/v1/ingest/shortcut", json=body, headers=auth)
    assert first.status_code == 201 and second.status_code == 200
    assert second.json()["duplicate"] is True
    assert len(scheduled) == 1, "重复消息不得再次触发转发"
