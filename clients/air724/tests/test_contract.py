from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_air724_client_uses_official_socket_runtime_and_relay_endpoints() -> None:
    source = (ROOT / "main.lua").read_text(encoding="utf-8")

    assert 'require "socket"' in source
    assert "socket.tcp(CONFIG.tls)" in source
    assert "websocket.create" not in source
    assert 'ws_path = "/api/v1/ws/otp"' in source
    assert 'claim_path = "/api/v1/otp/claim"' in source


def test_air724_client_authenticates_and_subscribes_with_script_token() -> None:
    source = (ROOT / "main.lua").read_text(encoding="utf-8")

    assert 'type = "auth", token = CONFIG.script_api_token' in source
    assert 'type = "subscribe"' in source
    assert "phone_number = CONFIG.phone_number" in source
    assert "CONFIG.device_id" in source
    assert "CONFIG.sender" in source
    assert "CONFIG.keyword" in source


def test_air724_client_deduplicates_and_claims_only_after_reconnect() -> None:
    source = (ROOT / "main.lua").read_text(encoding="utf-8")

    assert "seen_otp_ids" in source
    assert "seen_otp_order" in source
    assert "max_seen_otp_ids" in source
    assert "event.otp_id" in source
    assert "last_otp_created_at" in source
    assert "event.created_at <= last_otp_created_at" in source
    assert "local function recover_after_reconnect()" in source
    assert "relay_url(CONFIG.claim_path)" in source
    assert source.count("claim_json()") == 2  # function definition + reconnect call
    assert "local subscribed, recovered = false, false" in source
    assert "event_type == \"subscribed\"" in source
    assert "if is_reconnect and subscribed and not recovered then" in source
    assert "math.min(reconnect_seconds * 2, CONFIG.reconnect_max_seconds)" in source


def test_air724_download_configuration_is_explicit() -> None:
    source = (ROOT / "main.lua").read_text(encoding="utf-8")

    for field in (
        "host",
        "port",
        "tls",
        "script_api_token",
        "phone_number",
        "reconnect_max_seconds",
        "ping_seconds",
    ):
        assert field in source
    assert "PHONE_NUMBER" not in source


def test_air724_mqtt_ingest_is_an_independent_upload_client() -> None:
    source = (ROOT / "mqtt_ingest.lua").read_text(encoding="utf-8")

    assert 'require "mqtt"' in source
    assert 'require "sms"' in source
    assert 'sms.setNewSmsCb' in source
    assert 'source":"air724_mqtt"' in source
    assert 'relay/v1/devices/' in source
    assert 'os.date("!%Y-%m-%dT%H:%M:%SZ", os.time())' in source
    assert 'mqttc:publish(topic(), envelope, 1, 0)' in source
    assert 'retry_seconds = 1' in source
    assert 'retry_seconds * 2' in source
    assert 'require "websocket"' not in source


def test_air724_mqtt_ingest_does_not_log_message_content_or_secret() -> None:
    source = (ROOT / "mqtt_ingest.lua").read_text(encoding="utf-8")

    assert 'log_info("sms_forwarded")' in source
    assert 'log_info("sms_forwarded", content)' not in source
    assert 'log_info("sms_forwarded", CONFIG.hmac_secret)' not in source
