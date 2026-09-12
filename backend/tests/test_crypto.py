import base64

import pytest
from cryptography.exceptions import InvalidTag

from app.core.crypto import EncryptedValue, FieldCrypto


def test_aes_gcm_round_trip_requires_matching_aad() -> None:
    crypto = FieldCrypto(base64.urlsafe_b64encode(b"k" * 32).decode())
    value = crypto.encrypt_text("sample-secret-value", "relay:1:value:v1")
    assert isinstance(value, EncryptedValue)
    assert crypto.decrypt_text(value, "relay:1:value:v1") == "sample-secret-value"
    with pytest.raises(InvalidTag):
        crypto.decrypt_text(value, "relay:2:value:v1")


def test_aes_gcm_uses_a_fresh_nonce() -> None:
    crypto = FieldCrypto(base64.urlsafe_b64encode(b"n" * 32).decode())
    first = crypto.encrypt_text("same-value", "relay:1:value:v1")
    second = crypto.encrypt_text("same-value", "relay:1:value:v1")
    assert first.nonce != second.nonce
    assert first.ciphertext != second.ciphertext
