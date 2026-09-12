import base64
import binascii
import ntpath
import posixpath
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    admin_token: str
    admin_username: str = "admin"
    admin_password: str = ""
    database_url: str
    data_encryption_key: str
    platform_version: str = "1.1.0"
    public_base_url: str = ""
    relay_site: str = ":80"
    http_port: int = Field(default=80, ge=1, le=65535)
    https_port: int = Field(default=443, ge=1, le=65535)
    app_port: int = Field(default=8000, ge=1, le=65535)
    device_online_window_seconds: int = Field(default=180, ge=30, le=3600)
    device_conflict_window_seconds: int = Field(default=600, ge=60, le=86400)
    retention_days: int = Field(default=7, ge=1, le=3650)
    retention_interval_seconds: int = Field(default=3600, ge=1, le=86400)
    otp_ttl_seconds: int = Field(default=1800, ge=1, le=86400)
    claimed_otp_retention_seconds: int = Field(default=86400, ge=1, le=604800)
    hmac_skew_seconds: int = Field(default=300, ge=1, le=3600)
    release_manifest_path: str = ""
    mqtt_bridge_token: str = ""

    @field_validator("admin_token")
    @classmethod
    def validate_admin_token(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("ADMIN_TOKEN must contain at least 32 characters")
        return value

    @field_validator("admin_username")
    @classmethod
    def validate_admin_username(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 80:
            raise ValueError("ADMIN_USERNAME must contain 1 to 80 characters")
        return normalized

    @field_validator("public_base_url")
    @classmethod
    def validate_public_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized:
            return ""
        parsed = urlsplit(normalized)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("PUBLIC_BASE_URL must be an HTTP(S) origin without credentials")
        return normalized

    @field_validator("data_encryption_key")
    @classmethod
    def validate_data_encryption_key(cls, value: str) -> str:
        try:
            decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        except (binascii.Error, ValueError) as exc:
            raise ValueError("DATA_ENCRYPTION_KEY must be base64url") from exc
        if len(decoded) != 32:
            raise ValueError("DATA_ENCRYPTION_KEY must decode to 32 bytes")
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or normalized == "__DATABASE_URL_REQUIRED__":
            raise ValueError("DATABASE_URL must be set")
        try:
            parsed = make_url(normalized)
        except Exception as exc:
            raise ValueError("DATABASE_URL must be a valid SQLAlchemy URL") from exc

        if parsed.drivername.startswith("sqlite"):
            database = parsed.database
            if database == ":memory:":
                return normalized
            if not database or not (
                Path(database).is_absolute()
                or ntpath.isabs(database)
                or posixpath.isabs(database)
            ):
                raise ValueError("SQLite DATABASE_URL file path must be absolute")
        return normalized


settings = Settings()  # type: ignore[call-arg]  # Values are supplied by BaseSettings.
