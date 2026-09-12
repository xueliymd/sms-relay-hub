"""Reference client for non-Air724 Script API consumers."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen


def _with_path(base_url: str, path: str) -> str:
    parts = urlsplit(base_url.rstrip("/"))
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("base_url must be an absolute http(s) URL")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _normalize_phone(phone_number: str) -> str:
    digits = "".join(character for character in phone_number if character.isdigit())
    if not digits:
        raise ValueError("phone_number must contain digits")
    return ("+" if phone_number.strip().startswith("+") else "") + digits


@dataclass(frozen=True, slots=True)
class OtpEvent:
    otp_id: str
    message_id: str
    code: str
    created_at: str | None
    expires_at: str | None


class ScriptApiClient:
    def __init__(
        self,
        base_url: str,
        script_api_token: str,
        phone_number: str,
        *,
        device_id: str | None = None,
        sender: str | None = None,
        keyword: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.script_api_token = script_api_token
        self.phone_number = _normalize_phone(phone_number)
        self.device_id = device_id
        self.sender = sender
        self.keyword = keyword

    @property
    def websocket_url(self) -> str:
        url = _with_path(self.base_url, "/api/v1/ws/otp")
        parts = urlsplit(url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        return urlunsplit((scheme, parts.netloc, parts.path, parts.query, parts.fragment))

    @property
    def claim_url(self) -> str:
        return _with_path(self.base_url, "/api/v1/otp/claim")

    @property
    def claim_text_url(self) -> str:
        path = _with_path(self.base_url, "/api/v1/otp/claim.txt")
        return f"{path}?phone_number={quote(self.phone_number)}"

    def auth_frame(self) -> dict[str, str]:
        return {"type": "auth", "token": self.script_api_token}

    def subscription_frame(self) -> dict[str, str]:
        return build_subscription_frame(self)

    def claim_payload(self) -> dict[str, str]:
        payload: dict[str, str] = {"phone_number": self.phone_number}
        for key in ("device_id", "sender", "keyword"):
            value = getattr(self, key)
            if value:
                payload[key] = value
        return payload

    def authorization_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.script_api_token}"}

    def listen_once(
        self,
        *,
        timeout_seconds: float = 60,
        ping_interval_seconds: float = 25,
        connector: Callable[..., Any] | None = None,
        on_subscribed: Callable[[dict[str, Any]], None] | None = None,
    ) -> OtpEvent:
        if ping_interval_seconds <= 0:
            raise ValueError("ping_interval_seconds must be greater than zero")
        if connector is None:
            from websockets.sync.client import connect

            connector = connect
        deadline = time.monotonic() + timeout_seconds
        with connector(
            self.websocket_url,
            open_timeout=min(timeout_seconds, 10),
            close_timeout=5,
            ping_interval=None,
        ) as websocket:
            websocket.send(json.dumps(self.auth_frame(), separators=(",", ":")))
            auth = _receive_json(websocket, deadline)
            if auth.get("type") != "auth_ok":
                raise RuntimeError(f"websocket authentication failed: {auth}")

            websocket.send(json.dumps(self.subscription_frame(), separators=(",", ":")))
            subscribed = _receive_json(websocket, deadline)
            if subscribed.get("type") != "subscribed":
                raise RuntimeError(f"websocket subscription failed: {subscribed}")
            if on_subscribed is not None:
                on_subscribed(subscribed)

            next_ping_at = time.monotonic() + ping_interval_seconds
            while True:
                now = time.monotonic()
                if now >= deadline:
                    raise TimeoutError("timed out waiting for an OTP event")
                if now >= next_ping_at:
                    websocket.send(json.dumps({"type": "ping"}, separators=(",", ":")))
                    next_ping_at = now + ping_interval_seconds
                try:
                    frame = _receive_json(websocket, min(deadline, next_ping_at))
                except TimeoutError:
                    now = time.monotonic()
                    if now >= deadline:
                        raise
                    websocket.send(json.dumps({"type": "ping"}, separators=(",", ":")))
                    next_ping_at = now + ping_interval_seconds
                    continue
                if frame.get("type") == "otp":
                    return parse_otp_event(frame)

    def claim_latest(
        self,
        *,
        timeout_seconds: float = 10,
        opener: Callable[..., Any] = urlopen,
    ) -> OtpEvent | None:
        request = Request(
            self.claim_url,
            data=json.dumps(self.claim_payload(), separators=(",", ":")).encode(),
            headers={**self.authorization_headers(), "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with opener(request, timeout=timeout_seconds) as response:
                payload = json.loads(response.read())
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        payload["type"] = "otp"
        return parse_otp_event(payload)


def build_subscription_frame(client: ScriptApiClient) -> dict[str, str]:
    frame: dict[str, str] = {"type": "subscribe", "phone_number": client.phone_number}
    for key in ("device_id", "sender", "keyword"):
        value = getattr(client, key)
        if value:
            frame[key] = value
    return frame


def parse_otp_event(frame: dict[str, Any]) -> OtpEvent:
    if frame.get("type") != "otp":
        raise ValueError("frame is not an otp event")
    for field in ("otp_id", "message_id", "code"):
        value = frame.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"otp event field {field} is required")
    return OtpEvent(
        otp_id=frame["otp_id"],
        message_id=frame["message_id"],
        code=frame["code"],
        created_at=frame.get("created_at"),
        expires_at=frame.get("expires_at"),
    )


def _receive_json(websocket: Any, deadline: float) -> dict[str, Any]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("timed out waiting for an OTP event")
    payload = json.loads(websocket.recv(timeout=remaining))
    if not isinstance(payload, dict):
        raise TypeError("websocket frame must be a JSON object")
    return payload
