"""In-process full-message delivery for authenticated administrator clients."""

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AdminMessageSubscription:
    device_id: str | None = None
    sim_slot: int | None = None

    def __post_init__(self) -> None:
        normalized_device_id = None if self.device_id is None else str(self.device_id).strip()
        if normalized_device_id == "":
            raise ValueError("device_id must not be empty")
        if self.sim_slot is not None and (
            isinstance(self.sim_slot, bool)
            or not isinstance(self.sim_slot, int)
            or self.sim_slot < 0
        ):
            raise ValueError("sim_slot must be a non-negative integer")
        object.__setattr__(self, "device_id", normalized_device_id)

    def matches(self, event: dict[str, Any]) -> bool:
        raw_device_id = event.get("device_id")
        if self.device_id is not None and (
            raw_device_id is None or str(raw_device_id) != self.device_id
        ):
            return False
        return self.sim_slot is None or event.get("sim_slot") == self.sim_slot


@dataclass(slots=True)
class _Connection:
    websocket: Any
    subscription: AdminMessageSubscription


class AdminMessageWebSocketBroker:
    def __init__(self, *, send_timeout_seconds: float = 5.0) -> None:
        self._connections: dict[int, _Connection] = {}
        self._lock = asyncio.Lock()
        self.send_timeout_seconds = send_timeout_seconds

    async def add(self, websocket: Any, subscription: AdminMessageSubscription) -> None:
        async with self._lock:
            self._connections[id(websocket)] = _Connection(websocket, subscription)

    async def remove(self, websocket: Any) -> None:
        async with self._lock:
            self._connections.pop(id(websocket), None)

    async def count(self) -> int:
        async with self._lock:
            return len(self._connections)

    async def publish(self, event: dict[str, Any]) -> int:
        async with self._lock:
            connections = list(self._connections.values())

        async def send_one(connection: _Connection) -> bool:
            if not connection.subscription.matches(event):
                return False
            try:
                await asyncio.wait_for(
                    connection.websocket.send_json(event),
                    timeout=self.send_timeout_seconds,
                )
            except Exception:
                await self.remove(connection.websocket)
                return False
            return True

        results = await asyncio.gather(*(send_one(connection) for connection in connections))
        return sum(results)


admin_message_broker = AdminMessageWebSocketBroker()
