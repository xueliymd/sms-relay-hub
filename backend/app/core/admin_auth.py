import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings

SESSION_PREFIX = "srh_admin_v1"
SESSION_TTL_SECONDS = 8 * 60 * 60
password_hasher = PasswordHasher()


@dataclass(frozen=True, slots=True)
class AdminSessionClaims:
    account_id: str
    username: str
    session_version: int
    expires_at: int


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return password_hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def issue_admin_session(
    account_id: str,
    username: str,
    session_version: int,
    *,
    now: int | None = None,
) -> tuple[str, int]:
    issued_at = int(time.time()) if now is None else now
    expires_at = issued_at + SESSION_TTL_SECONDS
    payload = {
        "sub": account_id,
        "username": username,
        "sv": session_version,
        "iat": issued_at,
        "exp": expires_at,
    }
    encoded_payload = _encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    )
    signing_input = f"{SESSION_PREFIX}.{encoded_payload}".encode()
    signature = hmac.new(
        settings.admin_token.encode(), signing_input, hashlib.sha256
    ).digest()
    return f"{SESSION_PREFIX}.{encoded_payload}.{_encode(signature)}", expires_at


def parse_admin_session(token: str, *, now: int | None = None) -> AdminSessionClaims | None:
    try:
        prefix, encoded_payload, encoded_signature = token.split(".", 2)
        if prefix != SESSION_PREFIX:
            return None
        signing_input = f"{prefix}.{encoded_payload}".encode()
        expected = hmac.new(
            settings.admin_token.encode(), signing_input, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _decode(encoded_signature)):
            return None
        payload = json.loads(_decode(encoded_payload))
        account_id = payload["sub"]
        username = payload["username"]
        session_version = payload["sv"]
        expires_at = payload["exp"]
        if not isinstance(account_id, str) or not isinstance(username, str):
            return None
        if not isinstance(session_version, int) or not isinstance(expires_at, int):
            return None
        current_time = int(time.time()) if now is None else now
        if expires_at <= current_time:
            return None
        return AdminSessionClaims(
            account_id=account_id,
            username=username,
            session_version=session_version,
            expires_at=expires_at,
        )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
