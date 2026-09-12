"""短信转发规则引擎：入库消息按规则匹配后推送到外部渠道（多转发渠道）。

设计要点：
- 纯平台侧能力：入库（非重复）消息在发布管理端流后由本引擎异步分发，
  不阻塞入库响应；进程内任务，重试覆盖瞬时故障，失败落 forwarding_logs。
- 渠道适配统一为 (ok, status_code, message) 返回：>=500 或网络异常按瞬态
  退避重试，4xx 视为永久失败不重试。
- 模板变量：{{sender}} {{body}} {{phone}} {{code}} {{received_at}}；未配置
  模板时使用默认格式。{{code}} 取该消息命中的 OTP 验证码（如有）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import requests

FORWARDING_CHANNELS = ("wecom", "dingtalk", "feishu", "bark", "webhook")
FORWARDING_CHANNEL_LABELS = {
    "wecom": "企业微信",
    "dingtalk": "钉钉机器人",
    "feishu": "飞书机器人",
    "bark": "Bark",
    "webhook": "通用 Webhook",
}
FORWARDING_ATTEMPTS = 3
FORWARDING_BACKOFF_SECONDS = (2, 8)
FORWARDING_TIMEOUT_SECONDS = 10

_TEMPLATE_VARIABLES = ("sender", "body", "phone", "code", "received_at")


def validate_forwarding_target(channel: str, target: str) -> str:
    """按渠道校验推送目标地址（与 cmcc_login_status 的企微校验同强度）。"""
    from urllib.parse import parse_qs, urlsplit

    channel = str(channel or "wecom").strip().lower()
    target = str(target or "").strip()
    if channel == "wecom":
        if not target:
            raise ValueError("推送目标不能为空")
        parts = urlsplit(target)
        key = (parse_qs(parts.query).get("key") or [""])[0]
        if (
            parts.scheme != "https"
            or parts.netloc.lower() != "qyapi.weixin.qq.com"
            or parts.path != "/cgi-bin/webhook/send"
            or not key
        ):
            raise ValueError("企业微信地址格式不正确（https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...）")
        return target
    if not target:
        raise ValueError("推送目标不能为空")
    parts = urlsplit(target)
    if parts.scheme not in {"https", "http"} or not parts.netloc:
        raise ValueError("推送目标必须是 http(s) 地址")
    if channel == "dingtalk":
        if (
            parts.scheme != "https"
            or parts.netloc.lower() != "oapi.dingtalk.com"
            or parts.path != "/robot/send"
            or not (parse_qs(parts.query).get("access_token") or [""])[0]
        ):
            raise ValueError("钉钉机器人地址格式不正确（https://oapi.dingtalk.com/robot/send?access_token=...）")
    elif channel == "feishu":
        hook_prefix = "/open-apis/bot/v2/hook/"
        if (
            parts.scheme != "https"
            or parts.netloc.lower() != "open.feishu.cn"
            or not parts.path.startswith(hook_prefix)
            or len(parts.path) <= len(hook_prefix)
        ):
            raise ValueError("飞书机器人地址格式不正确（https://open.feishu.cn/open-apis/bot/v2/hook/xxx）")
    elif channel == "bark":
        if parts.path in ("", "/"):
            raise ValueError("Bark 地址缺少推送 Key（例如 https://api.day.app/你的Key）")
    elif channel == "webhook":
        pass
    else:
        raise ValueError(f"不支持的推送渠道: {channel}")
    return target


def mask_forwarding_target(target: str) -> str:
    """脱敏推送目标：保留协议与主机，路径与参数打码。"""
    from urllib.parse import urlsplit

    text = str(target or "").strip()
    if not text:
        return ""
    parts = urlsplit(text)
    if parts.netloc:
        return f"{parts.scheme}://{parts.netloc}/***"
    return text[:24] + "***"


def render_forwarding_content(template: str | None, variables: dict[str, str]) -> str:
    """渲染转发内容；未配置模板时使用默认格式。未知变量原样保留。"""
    if template and template.strip():
        content = str(template)
        for key in _TEMPLATE_VARIABLES:
            content = content.replace("{{" + key + "}}", str(variables.get(key, ""))
            )
        return content.strip() or str(variables.get("body") or "")
    phone = str(variables.get("phone") or "")
    code = str(variables.get("code") or "")
    lines = [f"发送方：{variables.get('sender') or '-'}", f"内容：{variables.get('body') or '-'}"]
    if phone:
        lines.append(f"接收：{phone}")
    if code:
        lines.append(f"验证码：{code}")
    return "\n".join(lines)


def match_forwarding_rule(rule: dict[str, Any], *, device_id: str, receiving_phone_lookup_hash: str | None, sender: str, body: str) -> bool:
    """规则匹配：全部已填条件都满足才算命中（未填 = 不限）。"""
    if rule.get("match_device_id") and str(message_device_id(device_id)) != str(rule["match_device_id"]):
        return False
    rule_phone = str(rule.get("match_phone_lookup_hash") or "")
    if rule_phone and receiving_phone_lookup_hash != rule_phone:
        return False
    rule_sender = str(rule.get("match_sender") or "").strip().lower()
    if rule_sender and rule_sender not in sender.lower():
        return False
    keywords = [
        keyword.strip().lower()
        for keyword in str(rule.get("match_keyword") or "").split(",")
        if keyword.strip()
    ]
    if keywords:
        body_lower = body.lower()
        if not any(keyword in body_lower for keyword in keywords):
            return False
    return True


def message_device_id(device_id: Any) -> str:
    return str(device_id) if device_id else ""


def send_to_channel(channel: str, target: str, title: str, content: str, *, timeout: int = 10) -> tuple[bool, int, str]:
    """发送到指定渠道。返回 (ok, http_status, message)；不抛网络异常。"""
    channel = str(channel or "wecom").strip().lower()
    text = f"{title}\n\n{content}".strip()[:3900]
    try:
        if channel == "wecom":
            response = requests.post(
                target, json={"msgtype": "text", "text": {"content": text}}, timeout=timeout
            )
            data = _response_json(response)
            ok = response.ok and str(data.get("errcode", "0")) == "0"
            return ok, response.status_code, str(data.get("errmsg") or "ok")
        if channel == "dingtalk":
            response = requests.post(
                target, json={"msgtype": "text", "text": {"content": text}}, timeout=timeout
            )
            data = _response_json(response)
            ok = response.ok and str(data.get("errcode", "0")) == "0"
            return ok, response.status_code, str(data.get("errmsg") or "ok")
        if channel == "feishu":
            response = requests.post(
                target, json={"msg_type": "text", "content": {"text": text}}, timeout=timeout
            )
            data = _response_json(response)
            ok = response.ok and (str(data.get("code")) == "0" or str(data.get("StatusCode")) == "0")
            return ok, response.status_code, str(data.get("msg") or data.get("StatusMessage") or "ok")
        if channel == "bark":
            response = requests.post(
                target, json={"title": title, "body": content, "group": "sms-relay"}, timeout=timeout
            )
            data = _response_json(response)
            ok = response.ok and str(data.get("code", "200")) == "200"
            return ok, response.status_code, "bark ok" if ok else "bark rejected"
        if channel == "webhook":
            response = requests.post(target, json={"title": title, "content": content}, timeout=timeout)
            ok = 200 <= response.status_code < 300
            return ok, response.status_code, f"HTTP {response.status_code}"
        return False, 0, f"不支持的推送渠道: {channel}"
    except requests.RequestException as exc:
        return False, 0, f"网络异常: {exc}"


def _response_json(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except Exception:
        data = {"raw": str(getattr(response, "text", ""))[:300]}
    return data if isinstance(data, dict) else {"raw": str(data)[:300]}


class ForwardingEngine:
    """入库消息的多渠道转发调度器（进程内异步任务 + 退避重试 + 发送日志）。"""

    def __init__(self, store: Any) -> None:
        self.store = store

    def schedule(self, message: Any) -> None:
        """入库侧调用：非重复消息排队分发。不在事件循环内时静默跳过。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.process(message))

    async def process(self, message: Any) -> None:
        try:
            rules = await asyncio.to_thread(self.store.list_forwarding_rules, enabled_only=True)
        except Exception:
            return
        if not rules:
            return
        for rule in rules:
            try:
                if not match_forwarding_rule(
                    rule,
                    device_id=message.device_id,
                    receiving_phone_lookup_hash=getattr(message, "receiving_phone_lookup_hash", None),
                    sender=message.sender,
                    body=message.body,
                ):
                    continue
                await self._deliver(rule, message)
            except Exception:
                continue

    async def _deliver(self, rule: dict[str, Any], message: Any) -> None:
        variables = await self._build_variables(rule, message)
        title = f"【短信转发】{message.sender}"
        content = render_forwarding_content(rule.get("template"), variables)
        target = str(rule.get("target") or "")
        channel = str(rule.get("channel") or "wecom")
        attempts = 0
        message_text = ""
        status = "failed"
        for attempt in range(1, FORWARDING_ATTEMPTS + 1):
            attempts = attempt
            ok, status_code, message_text = await asyncio.to_thread(
                send_to_channel, channel, target, title, content,
                timeout=FORWARDING_TIMEOUT_SECONDS,
            )
            if ok:
                status = "success"
                break
            if 400 <= status_code < 500 and status_code != 429:
                status = "failed"
                break
            status = "retrying" if attempt < FORWARDING_ATTEMPTS else "failed"
            if attempt < FORWARDING_ATTEMPTS:
                await asyncio.sleep(FORWARDING_BACKOFF_SECONDS[min(attempt - 1, len(FORWARDING_BACKOFF_SECONDS) - 1)])
        try:
            await asyncio.to_thread(
                self.store.record_forwarding_log,
                rule_id=rule.get("id"),
                rule_name=str(rule.get("name") or ""),
                message_id=str(message.id),
                channel=channel,
                target_masked=mask_forwarding_target(target),
                status=status,
                attempts=attempts,
                error=None if status == "success" else message_text,
            )
        except Exception:
            pass

    async def _build_variables(self, rule: dict[str, Any], message: Any) -> dict[str, str]:
        phone = str(getattr(message, "receiving_phone", "") or "")
        code = ""
        try:
            otp = await asyncio.to_thread(self.store.get_otp_delivery, message.id)
            if otp:
                code = str(otp.get("code") or "")
        except Exception:
            code = ""
        return {
            "sender": str(message.sender or ""),
            "body": str(message.body or ""),
            "phone": phone,
            "code": code,
            "received_at": format_received_at(message),
        }


def format_received_at(message: Any) -> str:
    received_at = getattr(message, "received_at", None)
    return received_at.strftime("%Y-%m-%d %H:%M:%S") if received_at is not None else ""


forwarding_engine = ForwardingEngine(None)


def bind_engine(store: Any) -> None:
    """应用装配时绑定 store（避免循环导入）。"""
    forwarding_engine.store = store
