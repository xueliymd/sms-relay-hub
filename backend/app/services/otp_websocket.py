"""In-process live OTP delivery for script clients.

The broker deliberately has no backlog. A reconnecting client must use the
HTTP claim endpoint once to recover codes that arrived while its socket was
down, then subscribe again for new events.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

from app.services.store import normalize_phone_number


@dataclass(frozen=True, slots=True)
class OtpSubscription:
    phone_number: str
    device_id: str | None = None
    sender: str | None = None
    keyword: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "phone_number", normalize_phone_number(self.phone_number))
        if self.device_id is not None:
            object.__setattr__(self, "device_id", str(self.device_id))
        if self.sender is not None:
            normalized_sender = self.sender.strip()
            object.__setattr__(self, "sender", normalized_sender or None)
        if self.keyword is not None:
            normalized_keyword = self.keyword.strip()
            object.__setattr__(self, "keyword", normalized_keyword or None)

    def matches(self, event: dict[str, Any]) -> bool:
        raw_phone = event.get("phone_number")
        if not isinstance(raw_phone, str):
            return False
        # 事件里的号码来自消息路由字段，历史消息可能仍是 E.164 统一之前
        # 的裸 11 位，所以比较前两侧都规范化。
        try:
            event_phone = normalize_phone_number(raw_phone)
        except ValueError:
            return False
        if event_phone != self.phone_number:
            return False
        if self.device_id is not None and str(event.get("device_id")) != self.device_id:
            return False
        sender = str(event.get("sender", ""))
        if self.sender is not None and self.sender.casefold() not in sender.casefold():
            return False
        body = str(event.get("body", ""))
        if self.keyword is not None and self.keyword.casefold() not in body.casefold():
            return False
        return True


@dataclass(slots=True)
class _Connection:
    websocket: Any
    subscription: OtpSubscription


class OtpWebSocketBroker:
    def __init__(self, *, send_timeout_seconds: float = 5.0) -> None:
        self._connections: dict[int, _Connection] = {}
        self._lock = asyncio.Lock()
        self.send_timeout_seconds = send_timeout_seconds

    async def add(self, websocket: Any, subscription: OtpSubscription) -> None:
        async with self._lock:
            self._connections[id(websocket)] = _Connection(websocket, subscription)

    async def remove(self, websocket: Any) -> None:
        async with self._lock:
            self._connections.pop(id(websocket), None)

    async def count(self) -> int:
        async with self._lock:
            return len(self._connections)

    async def publish(self, event: dict[str, Any]) -> None:
        async with self._lock:
            connections = list(self._connections.values())
        payload = {
            key: value
            for key, value in event.items()
            if key != "body" and value is not None
        }

        async def send_one(connection: _Connection) -> None:
            if not connection.subscription.matches(event):
                return
            try:
                await asyncio.wait_for(
                    connection.websocket.send_json(payload),
                    timeout=self.send_timeout_seconds,
                )
            except Exception:
                await self.remove(connection.websocket)

        await asyncio.gather(*(send_one(connection) for connection in connections))


otp_broker = OtpWebSocketBroker()
