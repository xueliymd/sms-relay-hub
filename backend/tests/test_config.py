import base64
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def make_settings(**values: object) -> Settings:
    constructor = cast(Any, Settings)
    return cast(Settings, constructor(**values))


def test_runtime_secrets_must_be_strong_and_decodable() -> None:
    with pytest.raises(ValidationError):
        make_settings(admin_token="short", data_encryption_key="not-base64")

    settings = make_settings(
        admin_token="neutral-admin-token-for-tests-123456",
        data_encryption_key=base64.urlsafe_b64encode(b"k" * 32).decode(),
        database_url="sqlite+pysqlite:///:memory:",
    )
    assert settings.admin_token.startswith("neutral-admin")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("retention_days", 0),
        ("retention_interval_seconds", 0),
        ("otp_ttl_seconds", 0),
        ("device_command_pending_ttl_seconds", 0),
        ("device_command_result_ttl_seconds", 0),
        ("claimed_otp_retention_seconds", 0),
        ("hmac_skew_seconds", 0),
        ("retention_days", -1),
    ],
)
def test_runtime_intervals_must_be_positive(field: str, value: int) -> None:
    common = {
        "admin_token": "neutral-admin-token-for-tests-123456",
        "data_encryption_key": base64.urlsafe_b64encode(b"k" * 32).decode(),
        "database_url": "sqlite+pysqlite:///:memory:",
    }

    with pytest.raises(ValidationError):
        make_settings(**common, **{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("http_port", 0),
        ("http_port", 65536),
        ("https_port", 0),
        ("https_port", 65536),
        ("app_port", 0),
        ("app_port", 65536),
    ],
)
def test_runtime_ports_must_be_valid(field: str, value: int) -> None:
    common = {
        "admin_token": "neutral-admin-token-for-tests-123456",
        "data_encryption_key": base64.urlsafe_b64encode(b"k" * 32).decode(),
        "database_url": "sqlite+pysqlite:///:memory:",
    }

    with pytest.raises(ValidationError):
        make_settings(**common, **{field: value})


def test_otp_and_device_command_default_ttls_are_explicit() -> None:
    common = {
        "admin_token": "neutral-admin-token-for-tests-123456",
        "data_encryption_key": base64.urlsafe_b64encode(b"k" * 32).decode(),
        "database_url": "sqlite+pysqlite:///:memory:",
    }
    settings = make_settings(**common)
    assert settings.otp_ttl_seconds == 600
    assert settings.device_command_pending_ttl_seconds == 600
    assert settings.device_command_result_ttl_seconds == 1800


def test_public_base_url_is_normalized_and_rejects_credentials() -> None:
    common = {
        "admin_token": "neutral-admin-token-for-tests-123456",
        "data_encryption_key": base64.urlsafe_b64encode(b"k" * 32).decode(),
        "database_url": "sqlite+pysqlite:///:memory:",
    }

    configured = make_settings(**common, public_base_url="https://sms.example.test/")
    assert configured.public_base_url == "https://sms.example.test"

    with pytest.raises(ValidationError, match="PUBLIC_BASE_URL"):
        make_settings(**common, public_base_url="https://admin:secret@sms.example.test")


def test_database_url_is_required_and_sqlite_file_urls_must_be_absolute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    common = {
        "admin_token": "neutral-admin-token-for-tests-123456",
        "data_encryption_key": base64.urlsafe_b64encode(b"k" * 32).decode(),
        "_env_file": None,
    }

    with pytest.raises(ValidationError, match="database_url"):
        make_settings(**common)

    with pytest.raises(ValidationError, match="absolute"):
        make_settings(**common, database_url="sqlite+pysqlite:///./relay-dev.db")

    settings = make_settings(
        **common,
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'preview.db').as_posix()}",
    )
    assert settings.database_url.endswith("/preview.db")


def test_settings_do_not_implicitly_load_dotenv_from_the_working_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for name in ("ADMIN_TOKEN", "DATA_ENCRYPTION_KEY", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text(
        "ADMIN_TOKEN=dotenv-admin-token-that-must-not-load\n"
        f"DATA_ENCRYPTION_KEY={base64.urlsafe_b64encode(b'd' * 32).decode()}\n"
        "DATABASE_URL=sqlite+pysqlite:///:memory:\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValidationError, match="admin_token|database_url|data_encryption_key"):
        make_settings()
