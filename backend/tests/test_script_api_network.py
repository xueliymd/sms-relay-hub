import base64
import hashlib
import hmac
import json
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from clients.script_api.otp_client import ScriptApiClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.store import store  # noqa: E402

ADMIN_TOKEN = "neutral-admin-token-for-tests-123456"


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _hmac_request(
    device_id: str,
    secret: str,
    payload: dict[str, object],
    *,
    nonce: str,
) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    key = base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))
    canonical = f"{timestamp}\n{nonce}\n{hashlib.sha256(raw).hexdigest()}".encode()
    signature = base64.urlsafe_b64encode(
        hmac.new(key, canonical, hashlib.sha256).digest()
    ).decode().rstrip("=")
    return raw, {
        "Content-Type": "application/json",
        "X-Device-Id": device_id,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
        "X-Idempotency-Key": str(payload["client_message_id"]),
    }


def test_external_script_receives_precise_otp_over_real_websocket(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'script-api-network.db').as_posix()}"
    store.configure(database_url, drop_existing=True)
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="on",
        )
    )
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started

    base_url = f"http://127.0.0.1:{port}"
    admin_headers = {"X-Admin-Token": ADMIN_TOKEN}
    try:
        with httpx.Client(base_url=base_url, timeout=5) as http:
            created = http.post(
                "/api/v1/admin/devices",
                json={
                    "name": "Script API network test",
                    "client_type": "root_module",
                    "auth_kind": "hmac",
                },
                headers=admin_headers,
            ).json()
            device_id = created["device"]["id"]
            device_secret = created["credential"]["secret"]
            sim_profile = http.put(
                f"/api/v1/admin/devices/{device_id}/sim-profiles/0",
                json={
                    "subscription_id": 5,
                    "phone_number": "+8613800138000",
                    "source": "manual",
                },
                headers=admin_headers,
            )
            assert sim_profile.status_code == 200
            api_token_response = http.post(
                "/api/v1/admin/api-clients",
                headers=admin_headers,
            )
            assert api_token_response.status_code == 201
            script_token = api_token_response.json()["token"]

            script = ScriptApiClient(
                base_url,
                script_token,
                "+86 138 0013 8000",
                device_id=device_id,
                sender="Relay Test",
                keyword="验证码",
            )
            subscribed = threading.Event()
            with ThreadPoolExecutor(max_workers=1) as executor:
                waiting = executor.submit(
                    script.listen_once,
                    timeout_seconds=5,
                    on_subscribed=lambda _frame: subscribed.set(),
                )
                assert subscribed.wait(3), "external script did not complete WebSocket subscription"

                payload: dict[str, object] = {
                    "schema_version": 1,
                    "client_message_id": "script-api-network-1",
                    "received_at": datetime.now(timezone.utc).isoformat(),
                    "sender": "Relay Test Service",
                    "body": "您的验证码是 739204，5分钟内有效。",
                    "source": "root_module",
                    "sim_slot": 0,
                    "metadata": {"subscription_id": 5},
                }
                raw, headers = _hmac_request(
                    device_id,
                    device_secret,
                    payload,
                    nonce="script-api-network-1",
                )
                started_at = time.perf_counter()
                ingested = http.post("/api/v1/ingest/messages", content=raw, headers=headers)
                event = waiting.result(timeout=5)
                delivery_seconds = time.perf_counter() - started_at

            assert ingested.status_code == 201
            assert event.code == "739204"
            assert event.message_id == ingested.json()["message_id"]
            assert delivery_seconds < 2
            print(f"websocket_delivery_ms={delivery_seconds * 1000:.1f}")

            claimed = script.claim_latest(timeout_seconds=5)
            assert claimed is not None
            assert claimed.code == "739204"
            assert claimed.message_id == event.message_id
            assert script.claim_latest(timeout_seconds=5) is None
    finally:
        server.should_exit = True
        server_thread.join(timeout=10)
        store.dispose()
