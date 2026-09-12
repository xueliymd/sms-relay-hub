from pathlib import Path

import pytest

from app.mqtt_bridge import build_forward_request, parse_bridge_config, should_redeliver


def test_build_forward_request_forwards_topic_and_body_without_payload_logging() -> None:
    request = build_forward_request(
        ingest_url="http://app:8000/api/v1/ingest/mqtt",
        bridge_token="bridge-token",
        topic="relay/v1/devices/device-1/messages",
        body=b'{"schema_version":"1"}',
    )

    assert request.full_url == "http://app:8000/api/v1/ingest/mqtt"
    assert request.headers["Authorization"] == "Bearer bridge-token"
    assert request.get_header("X-mqtt-topic") == "relay/v1/devices/device-1/messages"
    assert request.data == b'{"schema_version":"1"}'


def test_parse_bridge_config_requires_external_broker_and_bridge_token(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MQTT_BROKER_HOST",
        "RELAY_MQTT_BRIDGE_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="MQTT_BROKER_HOST"):
        parse_bridge_config()

    monkeypatch.setenv("MQTT_BROKER_HOST", "mqtt.example.test")
    with pytest.raises(ValueError, match="RELAY_MQTT_BRIDGE_TOKEN"):
        parse_bridge_config()


def test_deployment_declares_optional_mqtt_bridge_and_air724_uploader() -> None:
    root = Path(__file__).resolve().parents[2]
    compose = (root / "deploy" / "compose.yml").read_text(encoding="utf-8")
    env_example = (root / "deploy" / ".env.example").read_text(encoding="utf-8")
    uploader = root / "clients" / "air724" / "mqtt_ingest.lua"

    assert "mqtt-bridge:" in compose
    assert 'profiles: ["mqtt"]' in compose
    assert "MQTT_BROKER_HOST:" in compose
    assert "MQTT_CLIENT_ID:" in compose
    assert "RELAY_MQTT_BRIDGE_TOKEN:" in compose
    assert "MQTT_BROKER_HOST=" in env_example
    assert "MQTT_CLIENT_ID=" in env_example
    assert "RELAY_MQTT_BRIDGE_TOKEN=" in env_example
    assert uploader.is_file()


def test_bridge_redelivers_only_transient_forwarding_failures() -> None:
    assert should_redeliver(0)
    assert should_redeliver(408)
    assert should_redeliver(429)
    assert should_redeliver(500)
    assert should_redeliver(503)
    assert not should_redeliver(200)
    assert not should_redeliver(201)
    assert not should_redeliver(400)
    assert not should_redeliver(401)
    assert not should_redeliver(422)


def test_bridge_client_id_isolated_by_compose_project() -> None:
    config = parse_bridge_config(
        {
            "MQTT_BROKER_HOST": "mqtt.example.test",
            "RELAY_MQTT_BRIDGE_TOKEN": "bridge-token",
            "COMPOSE_PROJECT_NAME": "relay-two",
        }
    )

    assert config.client_id == "sms-relay-mqtt-bridge-relay-two"
