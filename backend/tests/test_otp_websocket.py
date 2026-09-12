import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.services.otp_websocket import OtpSubscription, OtpWebSocketBroker


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent.append(payload)


def test_broker_delivers_only_matching_phone_and_optional_filters() -> None:
    asyncio.run(_test_broker_delivers_only_matching_phone_and_optional_filters())


async def _test_broker_delivers_only_matching_phone_and_optional_filters() -> None:
    broker = OtpWebSocketBroker()
    socket = FakeWebSocket()
    subscription = OtpSubscription(phone_number="+15550100005", sender="Service", keyword="code")
    await broker.add(socket, subscription)

    await broker.publish(
        {
            "type": "otp",
            "otp_id": str(uuid4()),
            "message_id": str(uuid4()),
            "phone_number": "+15550100005",
            "sender": "Service Relay",
            "body": "Your access code is 482731",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "code": "482731",
        }
    )
    await broker.publish(
        {
            "type": "otp",
            "otp_id": str(uuid4()),
            "message_id": str(uuid4()),
            "phone_number": "+15550100006",
            "sender": "Service Relay",
            "body": "Your access code is 111111",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "code": "111111",
        }
    )

    assert [item["code"] for item in socket.sent] == ["482731"]


def test_broker_removes_socket_when_send_fails() -> None:
    asyncio.run(_test_broker_removes_socket_when_send_fails())


async def _test_broker_removes_socket_when_send_fails() -> None:
    broker = OtpWebSocketBroker()

    class BrokenSocket(FakeWebSocket):
        async def send_json(self, payload: dict[str, object]) -> None:
            raise RuntimeError("closed")

    socket = BrokenSocket()
    await broker.add(socket, OtpSubscription(phone_number="+15550100005"))
    await broker.publish({"type": "otp", "phone_number": "+15550100005", "code": "123456"})
    assert await broker.count() == 0


def test_slow_socket_does_not_block_other_subscribers() -> None:
    asyncio.run(_test_slow_socket_does_not_block_other_subscribers())


async def _test_slow_socket_does_not_block_other_subscribers() -> None:
    broker = OtpWebSocketBroker(send_timeout_seconds=1.0)
    slow_started = asyncio.Event()
    release_slow = asyncio.Event()
    fast_sent = asyncio.Event()

    class SlowSocket(FakeWebSocket):
        async def send_json(self, payload: dict[str, object]) -> None:
            slow_started.set()
            await release_slow.wait()
            await super().send_json(payload)

    class FastSocket(FakeWebSocket):
        async def send_json(self, payload: dict[str, object]) -> None:
            await super().send_json(payload)
            fast_sent.set()

    subscription = OtpSubscription(phone_number="+15550100005")
    await broker.add(SlowSocket(), subscription)
    await broker.add(FastSocket(), subscription)

    publish_task = asyncio.create_task(
        broker.publish({"type": "otp", "phone_number": "+15550100005", "code": "123456"})
    )
    await asyncio.wait_for(slow_started.wait(), timeout=0.1)
    await asyncio.wait_for(fast_sent.wait(), timeout=0.1)
    release_slow.set()
    await publish_task


def test_subscription_normalizes_phone_number() -> None:
    subscription = OtpSubscription(phone_number="+1 (555) 010-0005")
    assert subscription.phone_number == "+15550100005"


def test_subscription_normalizes_mainland_phone_number_variants() -> None:
    assert OtpSubscription(phone_number="13800138000").phone_number == "+8613800138000"
    assert OtpSubscription(phone_number="8613800138000").phone_number == "+8613800138000"
    assert OtpSubscription(phone_number="+8613800138000").phone_number == "+8613800138000"


def test_subscription_matches_event_phone_written_before_e164_unification() -> None:
    subscription = OtpSubscription(phone_number="13800138001")
    event = {
        "phone_number": "13800138001",
        "sender": "Service",
        "body": "code 482731",
    }
    assert subscription.matches(event)
