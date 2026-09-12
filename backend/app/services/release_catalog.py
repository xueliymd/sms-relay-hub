import json
import re
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from app.core.config import settings

HEX_64 = re.compile(r"^[0-9a-f]{64}$")
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def release_manifest_path() -> Path:
    configured = settings.release_manifest_path.strip()
    if configured:
        return Path(configured)
    packaged = Path(__file__).resolve().parents[1] / "static" / "assets" / "releases" / "release-catalog.json"
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[3] / "web" / "public" / "assets" / "releases" / "release-catalog.json"


def load_releases(path: Path | None = None) -> list[dict[str, Any]]:
    # utf-8-sig, not utf-8: it strips a leading BOM when one is present and behaves
    # exactly like utf-8 when it is not. json.loads rejects a BOM outright, and
    # Windows packaging tools write one by default, which used to degrade every
    # /api/v1/releases/... route to 503 release_catalog_invalid.
    payload = json.loads((path or release_manifest_path()).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported release catalog schema")
    raw_releases = payload.get("releases")
    if not isinstance(raw_releases, list):
        raise ValueError("release catalog must contain releases")

    releases: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for raw in raw_releases:
        release = validate_release(raw)
        key = (release["platform"], release["channel"], release["version_code"])
        if key in seen:
            raise ValueError("duplicate release version")
        seen.add(key)
        releases.append(release)
    return releases


def releases_for(platform: str, channel: str) -> list[dict[str, Any]]:
    return sorted(
        (
            release
            for release in load_releases()
            if release["platform"] == platform and release["channel"] == channel
        ),
        key=lambda release: release["version_code"],
        reverse=True,
    )


def validate_release(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("release must be an object")
    platform = required_name(raw, "platform")
    channel = required_name(raw, "channel")
    version_code = required_int(raw, "version_code", minimum=1)
    version_name = required_text(raw, "version_name", maximum=80)
    published_at = required_text(raw, "published_at", maximum=80)
    min_sdk = required_int(raw, "min_sdk", minimum=21, maximum=100)
    mandatory = raw.get("mandatory")
    if not isinstance(mandatory, bool):
        raise ValueError("mandatory must be boolean")
    notes = required_text(raw, "notes", maximum=4000)
    size_bytes = required_int(raw, "size_bytes", minimum=1, maximum=500 * 1024 * 1024)
    sha256 = required_hash(raw, "sha256")
    download_url = required_download_path(raw)
    signer_sha256 = (
        required_hash(raw, "signer_sha256")
        if download_url.lower().endswith(".apk")
        else optional_hash(raw, "signer_sha256")
    )
    return {
        "platform": platform,
        "channel": channel,
        "version_code": version_code,
        "version_name": version_name,
        "published_at": published_at,
        "min_sdk": min_sdk,
        "mandatory": mandatory,
        "notes": notes,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "signer_sha256": signer_sha256,
        "download_url": download_url,
    }


def required_name(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or NAME.fullmatch(value) is None:
        raise ValueError(f"{key} is invalid")
    return value


def required_text(raw: dict[str, Any], key: str, *, maximum: int) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} is invalid")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{key} is invalid")
    return normalized


def required_int(
    raw: dict[str, Any],
    key: str,
    *,
    minimum: int,
    maximum: int = 2_147_483_647,
) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{key} is invalid")
    return value


def required_hash(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} is invalid")
    normalized = value.strip().lower().replace(":", "")
    if HEX_64.fullmatch(normalized) is None:
        raise ValueError(f"{key} is invalid")
    return normalized


def optional_hash(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    return required_hash(raw, key)


def required_download_path(raw: dict[str, Any]) -> str:
    value = raw.get("download_url")
    if not isinstance(value, str) or not value.startswith("/assets/"):
        raise ValueError("download_url must be a local asset path")
    parsed = urlsplit(value)
    path = PurePosixPath(parsed.path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or ".." in path.parts:
        raise ValueError("download_url must be a local asset path")
    if path.suffix.lower() not in {".apk", ".zip"}:
        raise ValueError("download_url has an unsupported artifact type")
    return parsed.path
