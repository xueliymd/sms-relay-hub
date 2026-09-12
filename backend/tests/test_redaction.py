from app.core.redaction import (
    mask_identifier,
    mask_phone_number,
    redact_mapping,
    scrub_metadata,
)


def test_redaction_removes_sensitive_values_recursively() -> None:
    payload = {
        "device_id": "device-123456",
        "body": "sample message body",
        "authorization": "Bearer sample-token",
        "nested": {"access_code": "482731", "status": "accepted"},
    }
    redacted = redact_mapping(payload)
    assert redacted["body"] == "[REDACTED]"
    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["nested"]["access_code"] == "[REDACTED]"
    assert redacted["nested"]["status"] == "accepted"
    assert redacted["device_id"] == "device-123456"


def test_identifier_mask_keeps_only_a_short_suffix() -> None:
    assert mask_identifier("13800138000") == "*********00"
