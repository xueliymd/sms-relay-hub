import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT.parent))

from clients.script_api.otp_client import (
    ScriptApiClient,
    build_subscription_frame,
    parse_otp_event,
)


def test_script_api_client_derives_websocket_and_claim_urls() -> None:
    client = ScriptApiClient(
        "http://127.0.0.1:8000/",
        "script-token",
        "+86 138 0013 8000",
    )

    assert client.websocket_url == "ws://127.0.0.1:8000/api/v1/ws/otp"
    assert client.claim_url == "http://127.0.0.1:8000/api/v1/otp/claim"
    assert client.claim_text_url.startswith("http://127.0.0.1:8000/api/v1/otp/claim.txt?")
    assert "phone_number=%2B8613800138000" in client.claim_text_url


def test_script_api_client_builds_auth_subscription_and_filters_otp_event() -> None:
    client = ScriptApiClient(
        "https://relay.test",
        "script-token",
        "+8613800138000",
        device_id="device-1",
        sender="Service",
        keyword="验证码",
    )

    assert client.auth_frame() == {"type": "auth", "token": "script-token"}
    assert build_subscription_frame(client) == {
        "type": "subscribe",
        "phone_number": "+8613800138000",
        "device_id": "device-1",
        "sender": "Service",
        "keyword": "验证码",
    }
    assert parse_otp_event(
        {
            "type": "otp",
            "otp_id": "otp-1",
            "message_id": "message-1",
            "code": "123456",
            "created_at": "2026-08-23T10:00:00+00:00",
            "expires_at": "2026-08-23T10:30:00+00:00",
        }
    ).code == "123456"


def test_listen_once_completes_real_protocol_sequence() -> None:
    class FakeSocket:
        def __init__(self) -> None:
            self.sent: list[dict[str, str]] = []
            self.frames = iter(
                [
                    {"type": "auth_ok"},
                    {"type": "subscribed", "phone_number": "+8613800138000"},
                    {
                        "type": "otp",
                        "otp_id": "otp-live",
                        "message_id": "message-live",
                        "code": "654321",
                        "created_at": "2026-08-23T10:00:00+00:00",
                        "expires_at": "2026-08-23T10:30:00+00:00",
                    },
                ]
            )

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def send(self, payload: str) -> None:
            self.sent.append(json.loads(payload))

        def recv(self, timeout: float | None = None) -> str:
            assert timeout is None or timeout > 0
            return json.dumps(next(self.frames))

    socket = FakeSocket()
    client = ScriptApiClient("http://127.0.0.1:8000", "script-token", "+8613800138000")
    subscribed = []

    event = client.listen_once(
        timeout_seconds=2,
        connector=lambda *_args, **_kwargs: socket,
        on_subscribed=lambda frame: subscribed.append(frame),
    )

    assert socket.sent == [client.auth_frame(), client.subscription_frame()]
    assert subscribed == [{"type": "subscribed", "phone_number": "+8613800138000"}]
    assert event.code == "654321"


def test_listen_once_sends_application_ping_while_waiting() -> None:
    class FakeSocket:
        def __init__(self) -> None:
            self.sent: list[dict[str, str]] = []
            self.frames = [
                {"type": "auth_ok"},
                {"type": "subscribed", "phone_number": "+8613800138000"},
                {"type": "pong"},
                {
                    "type": "otp",
                    "otp_id": "otp-after-ping",
                    "message_id": "message-after-ping",
                    "code": "778899",
                    "created_at": "2026-08-23T10:00:00+00:00",
                    "expires_at": "2026-08-23T10:30:00+00:00",
                },
            ]
            self.receive_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def send(self, payload: str) -> None:
            self.sent.append(json.loads(payload))

        def recv(self, timeout: float | None = None) -> str:
            self.receive_count += 1
            if self.receive_count == 3:
                assert timeout is not None and timeout > 0
                time.sleep(timeout)
                raise TimeoutError
            return json.dumps(self.frames.pop(0))

    socket = FakeSocket()
    client = ScriptApiClient("http://127.0.0.1:8000", "script-token", "+8613800138000")

    event = client.listen_once(
        timeout_seconds=1,
        ping_interval_seconds=0.01,
        connector=lambda *_args, **_kwargs: socket,
    )

    assert socket.sent == [
        client.auth_frame(),
        client.subscription_frame(),
        {"type": "ping"},
    ]
    assert event.code == "778899"


def test_claim_latest_posts_filters_and_returns_none_for_404() -> None:
    class FakeResponse:
        def __init__(self, status: int, body: dict[str, str] | None = None) -> None:
            self.status = status
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(self.body).encode()

    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(
            200,
            {
                "otp_id": "otp-claim",
                "message_id": "message-claim",
                "code": "112233",
                "created_at": "2026-08-23T10:00:00+00:00",
                "expires_at": "2026-08-23T10:30:00+00:00",
            },
        )

    client = ScriptApiClient("http://127.0.0.1:8000", "script-token", "+8613800138000")
    event = client.claim_latest(timeout_seconds=3, opener=opener)

    request, timeout = requests[0]
    assert request.full_url == client.claim_url
    assert request.headers["Authorization"] == "Bearer script-token"
    assert json.loads(request.data) == {"phone_number": "+8613800138000"}
    assert timeout == 3
    assert event is not None and event.code == "112233"
