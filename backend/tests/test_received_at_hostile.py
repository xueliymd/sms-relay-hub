"""覆盖 DEFERRED_FEATURES 第五节：数组/对象/数字/乱字符串/嵌套 received_at → 全部 201。"""
import json
from typing import Any

from fastapi.testclient import TestClient

from tests.test_relay import ADMIN_HEADERS, create_device


def _post(client: TestClient, token: str, received_at: Any, n: int) -> int:
    payload = {"message": f"Code {100000 + n}"}
    payload["received_at"] = received_at
    return int(
        client.post(
        "/api/v1/ingest/shortcut",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        ).status_code
    )


def test_received_at_never_causes_422(client: TestClient) -> None:
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    token = created["credential"]["secret"]
    hostile = [
        [2026, 9, 7, 22, 15, 30],          # iOS 日期数组序列化
        {"date": "2026-09-07"},            # 对象
        1789080930,                        # epoch 秒
        1789080930000,                     # epoch 毫秒
        True,                              # bool
        "not-a-date",                      # 乱字符串
        "2026/09/07 22:15:30",             # 斜杠格式
        "2026年9月7日 22:15",              # 中文格式
        "2026-09-07 22:15:30",             # 无时区
        "",                                # 空串
        None,                              # null
        {"iso": "2026-09-07T22:15:30+08:00"},  # 嵌套对象
    ]
    for n, value in enumerate(hostile):
        status = _post(client, token, value, n)
        assert status in (200, 201), (
            f"received_at={json.dumps(value, ensure_ascii=False)} -> {status} (期望 201 入库或 200 幂等去重)"
        )


def test_same_sms_resend_with_different_received_at_is_deduplicated(
    client: TestClient,
) -> None:
    """同一短信跨分钟重发（设备时间不同）→ 幂等去重，不再出现双时间重复条目。"""
    created = create_device(client, client_type="ios_shortcut", auth_kind="bearer")
    token = created["credential"]["secret"]
    first = client.post(
        "/api/v1/ingest/shortcut",
        json={"message": "【移动】验证码：482731，请勿泄露。", "received_at": "2026-09-09T10:00:05+08:00"},
        headers={"Authorization": f"Bearer {token}"},
    )
    retry = client.post(
        "/api/v1/ingest/shortcut",
        json={"message": "【移动】验证码：482731，请勿泄露。", "received_at": "2026-09-09T10:01:35+08:00"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert first.status_code == 201
    assert retry.status_code == 200, "同一短信跨分钟重发必须命中幂等去重，而不是入库第二条"
    listed = client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json()
    bodies = [m["body"] for m in listed if "482731" in (m.get("body") or "")]
    assert len(bodies) == 1, f"应只有一条记录，实际 {len(bodies)} 条"
