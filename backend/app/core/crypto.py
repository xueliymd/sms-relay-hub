import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True, slots=True)
class EncryptedValue:
    nonce: bytes
    ciphertext: bytes
    key_version: int = 1


class FieldCrypto:
    def __init__(self, encoded_key: str) -> None:
        try:
            key = base64.urlsafe_b64decode(encoded_key + "=" * (-len(encoded_key) % 4))
        except (ValueError, TypeError) as exc:
            raise ValueError("DATA_ENCRYPTION_KEY must be valid base64url") from exc
        if len(key) != 32:
            raise ValueError("DATA_ENCRYPTION_KEY must decode to exactly 32 bytes")
        self._key = key
        self._cipher = AESGCM(key)

    def encrypt_text(self, value: str, aad: str) -> EncryptedValue:
        nonce = secrets.token_bytes(12)
        ciphertext = self._cipher.encrypt(nonce, value.encode("utf-8"), aad.encode("utf-8"))
        return EncryptedValue(nonce=nonce, ciphertext=ciphertext)

    def decrypt_text(self, value: EncryptedValue, aad: str) -> str:
        plaintext = self._cipher.decrypt(
            value.nonce,
            value.ciphertext,
            aad.encode("utf-8"),
        )
        return plaintext.decode("utf-8")

    def lookup_digest(self, value: str, domain: str) -> str:
        canonical = f"{domain}\0{value}".encode()
        return hmac.new(self._key, canonical, hashlib.sha256).hexdigest()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_bearer(prefix: str = "srh_dev_") -> str:
    return prefix + secrets.token_urlsafe(32)


def new_hmac_secret() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def verify_hmac(secret_b64: str, timestamp: str, nonce: str, body: bytes, signature: str) -> bool:
    try:
        secret = base64.urlsafe_b64decode(secret_b64 + "=" * (-len(secret_b64) % 4))
        given = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    except ValueError:
        return False
    digest = hashlib.sha256(body).hexdigest()
    expected = hmac.new(secret, f"{timestamp}\n{nonce}\n{digest}".encode(), hashlib.sha256).digest()
    return hmac.compare_digest(expected, given)
