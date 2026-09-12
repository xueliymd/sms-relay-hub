from collections.abc import Mapping
from typing import Any

SENSITIVE_KEYS = {
    "access_code",
    "authorization",
    "body",
    "code",
    "credential",
    "hmac_secret",
    "message",
    "otp",
    "password",
    "secret",
    "signature",
    "token",
}


def mask_identifier(value: str, visible_suffix: int = 2) -> str:
    if visible_suffix <= 0:
        return "*" * len(value)
    if len(value) <= visible_suffix:
        return "*" * len(value)
    return "*" * (len(value) - visible_suffix) + value[-visible_suffix:]


def redact_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, item in value.items():
        normalized = key.lower()
        if normalized in SENSITIVE_KEYS or normalized.endswith(("_token", "_secret", "_code")):
            redacted[key] = "[REDACTED]"
        elif isinstance(item, Mapping):
            redacted[key] = redact_mapping(item)
        elif isinstance(item, list):
            redacted[key] = [
                redact_mapping(entry) if isinstance(entry, Mapping) else entry for entry in item
            ]
        else:
            redacted[key] = item
    return redacted
