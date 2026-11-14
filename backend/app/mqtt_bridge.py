"""Optional MQTT-to-HTTP bridge for Air724 SMS upload clients."""

from __future__ import annotations

import logging
import os
import ssl
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast
from urllib.parse import urlsplit

LOGGER = logging.getLogger("sms_relay.mqtt_bridge")


@dataclass(frozen=True, slots=True)
class BridgeConfig:
    broker_host: str
    broker_port: int
    client_id: str
    transport: Literal["tcp", "websockets"]
    websocket_path: str
    topic_filter: str
    username: str
    password: str
    tls: bool
    ingest_url: str
    bridge_token: str
    http_timeout_seconds: float = 15.0


def _env(environ: Mapping[str, str], name: str, default: str = "") -> str:
    return environ.get(name, default).strip()


def _env_bool(environ: Mapping[str, str], name: str, default: bool = False) -> bool:
    value = _env(environ, name)
    if not value:
        return default
    if value.lower() in {"1", "true", "yes", "on"}:
        return True
    if value.lower() in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def parse_bridge_config(environ: Mapping[str, str] | None = None) -> BridgeConfig:
    values = os.environ if environ is None else environ
    broker_host = _env(values, "MQTT_BROKER_HOST")
    if not broker_host:
        raise ValueError("MQTT_BROKER_HOST is required")

    bridge_token = _env(values, "RELAY_MQTT_BRIDGE_TOKEN")
    if not bridge_token:
        raise ValueError("RELAY_MQTT_BRIDGE_TOKEN is required")

    try:
        broker_port = int(_env(values, "MQTT_BROKER_PORT", "1883"))
    except ValueError as exc:
        raise ValueError("MQTT_BROKER_PORT must be an integer") from exc
    if not 1 <= broker_port <= 65535:
        raise ValueError("MQTT_BROKER_PORT must be between 1 and 65535")

    transport = cast(Literal["tcp", "websockets"], _env(values, "MQTT_TRANSPORT", "tcp").lower())
    if transport not in {"tcp", "websockets"}:
        raise ValueError("MQTT_TRANSPORT must be tcp or websockets")

    websocket_path = _env(values, "MQTT_WS_PATH", "/websocket") or "/websocket"
    if not websocket_path.startswith("/"):
        raise ValueError("MQTT_WS_PATH must start with /")

    topic_filter = _env(values, "MQTT_TOPIC_FILTER", "relay/v1/devices/+/messages")
    if not topic_filter:
        raise ValueError("MQTT_TOPIC_FILTER is required")

    project_name = _env(values, "COMPOSE_PROJECT_NAME", "relay")
    client_id = _env(values, "MQTT_CLIENT_ID", f"sms-relay-mqtt-bridge-{project_name}")
    if not client_id:
        raise ValueError("MQTT_CLIENT_ID is required")

    ingest_url = _env(values, "RELAY_MQTT_INGEST_URL", "http://app:8000/api/v1/ingest/mqtt")
    parsed_ingest_url = urlsplit(ingest_url)
    if parsed_ingest_url.scheme not in {"http", "https"} or not parsed_ingest_url.netloc:
        raise ValueError("RELAY_MQTT_INGEST_URL must be an HTTP(S) URL")

    username = _env(values, "MQTT_USERNAME")
    password = _env(values, "MQTT_PASSWORD")
    if username and not password:
        raise ValueError("MQTT_PASSWORD is required when MQTT_USERNAME is set")

    return BridgeConfig(
        broker_host=broker_host,
        broker_port=broker_port,
        client_id=client_id,
        transport=transport,
        websocket_path=websocket_path,
        topic_filter=topic_filter,
        username=username,
        password=password,
        tls=_env_bool(values, "MQTT_TLS"),
        ingest_url=ingest_url,
        bridge_token=bridge_token,
    )


def build_forward_request(
    *, ingest_url: str, bridge_token: str, topic: str, body: bytes
) -> urllib.request.Request:
    """Build the internal request without decoding or logging the MQTT payload."""

    return urllib.request.Request(
        ingest_url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {bridge_token}",
            "Content-Type": "application/json",
            "X-MQTT-Topic": topic,
        },
    )


def forward_message(config: BridgeConfig, topic: str, body: bytes) -> int:
    request = build_forward_request(
        ingest_url=config.ingest_url,
        bridge_token=config.bridge_token,
        topic=topic,
        body=body,
    )
    try:
        with urllib.request.urlopen(request, timeout=config.http_timeout_seconds) as response:
            status_code = int(response.status)
    except urllib.error.HTTPError as error:
        status_code = int(error.code)
        LOGGER.warning("MQTT message rejected by relay status=%s", status_code)
        return status_code
    except urllib.error.URLError as error:
        LOGGER.warning("MQTT message forwarding failed reason=%s", error.reason)
        return 0

    if status_code not in {200, 201}:
        LOGGER.warning("MQTT message forwarding returned status=%s", status_code)
    else:
        LOGGER.info("MQTT message forwarded status=%s", status_code)
    return status_code


def should_redeliver(status_code: int) -> bool:
    return status_code == 0 or status_code in {408, 429} or status_code >= 500


def run(config: BridgeConfig) -> None:
    try:
        import paho.mqtt.client as mqtt
    except ImportError as error:
        raise RuntimeError("paho-mqtt is required for the MQTT bridge") from error

    callback_api_version = getattr(mqtt, "CallbackAPIVersion", None)
    if callback_api_version is None:
        raise RuntimeError("paho-mqtt 2.x is required for the MQTT bridge")
    client = mqtt.Client(
        callback_api_version.VERSION2,
        client_id=config.client_id,
        clean_session=False,
        transport=config.transport,
        manual_ack=True,
    )
    if config.username:
        client.username_pw_set(config.username, config.password)
    if config.tls:
        client.tls_set_context(ssl.create_default_context())
    if config.transport == "websockets":
        client.ws_set_options(path=config.websocket_path)

    def on_connect(
        _client: Any,
        _userdata: Any,
        _flags: Any,
        reason_code: Any,
        _properties: Any,
    ) -> None:
        if int(reason_code) != 0:
            LOGGER.error("MQTT broker rejected connection reason=%s", reason_code)
            return
        result, _message_id = _client.subscribe(config.topic_filter, qos=1)
        if result != mqtt.MQTT_ERR_SUCCESS:
            LOGGER.error("MQTT topic subscription failed result=%s", result)
        else:
            LOGGER.info("MQTT bridge subscribed topic=%s", config.topic_filter)

    def on_message(_client: Any, _userdata: Any, message: Any) -> None:
        status_code = forward_message(config, str(message.topic), bytes(message.payload))
        if should_redeliver(status_code):
            LOGGER.warning("MQTT message will be redelivered after reconnect status=%s", status_code)
            _client.disconnect()
            return
        result = _client.ack(message.mid, message.qos)
        if result != mqtt.MQTT_ERR_SUCCESS:
            LOGGER.warning("MQTT acknowledgement failed result=%s", result)
            _client.disconnect()

    def on_disconnect(
        _client: Any,
        _userdata: Any,
        _disconnect_flags: Any,
        reason_code: Any,
        _properties: Any,
    ) -> None:
        LOGGER.warning("MQTT bridge disconnected reason=%s", reason_code)

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect
    client.connect(config.broker_host, config.broker_port, keepalive=60)
    client.loop_forever(retry_first_connection=True)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
    run(parse_bridge_config())


if __name__ == "__main__":
    main()
