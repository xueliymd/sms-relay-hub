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

# Keys that carry a subscriber number. These are deliberately NOT in
# SENSITIVE_KEYS: a phone number is masked (last four kept) rather than fully
# redacted, so admin clients can still tell which SIM a message arrived on.
PHONE_KEYS = {
    "phone_number",
    "receiving_phone",
    "sender_phone",
    "msisdn",
}


def mask_identifier(value: str, visible_suffix: int = 2) -> str:
    if visible_suffix <= 0:
        return "*" * len(value)
    if len(value) <= visible_suffix:
        return "*" * len(value)
    return "*" * (len(value) - visible_suffix) + value[-visible_suffix:]


def mask_phone_number(phone_number: str | None) -> str | None:
    """Mask every character except the last four, preserving the length.

    Numbers with four or fewer digits are masked completely: keeping the last
    four of a short number would reveal the whole thing.
    """
    if phone_number is None:
        return None
    digit_count = sum(character.isdigit() for character in phone_number)
    if digit_count <= 4:
        return "*" * len(phone_number)
    return "*" * (len(phone_number) - 4) + phone_number[-4:]


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


def _mask_phone_values(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: (
                mask_phone_number(item)
                if key.lower() in PHONE_KEYS and isinstance(item, str)
                else _mask_phone_values(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_mask_phone_values(entry) for entry in value]
    return value


def scrub_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    """Make client-supplied metadata safe to return to admin clients.

    Metadata is attacker-influenced free-form JSON: an ingest payload can put a
    plaintext phone number under ``metadata["phone_number"]``, which used to be
    echoed back verbatim next to the masked ``receiving_phone_masked`` field.
    Secrets are redacted and phone numbers are masked, recursively, so metadata
    cannot act as a side channel around the API's masking rules.
    """
    scrubbed = _mask_phone_values(redact_mapping(value))
    return scrubbed if isinstance(scrubbed, dict) else {}
