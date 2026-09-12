from typing import Any

from fastapi import APIRouter, Query

from app.core.errors import RelayError
from app.services.release_catalog import releases_for

router = APIRouter(prefix="/api/v1/releases", tags=["releases"])
SUPPORTED_RELEASE_PLATFORMS = {"android", "manager", "root", "deploy"}


def android_releases(channel: str) -> list[dict[str, Any]]:
    return platform_releases("android", channel)


def platform_releases(platform: str, channel: str) -> list[dict[str, Any]]:
    if platform not in SUPPORTED_RELEASE_PLATFORMS:
        raise RelayError(
            404,
            "release_platform_not_supported",
            "Release platform is not supported",
        )
    try:
        return releases_for(platform, channel)
    except (OSError, ValueError, TypeError) as exc:
        raise RelayError(503, "release_catalog_invalid", "Release catalog is unavailable") from exc


@router.get("/android/latest")
def latest_android_release(
    channel: str = Query(default="stable", pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$"),
    current_version_code: int = Query(default=0, ge=0, le=2_147_483_647),
) -> dict[str, Any]:
    releases = android_releases(channel)
    if not releases:
        raise RelayError(404, "release_not_found", "No Android release is available")
    latest = releases[0]
    return {
        "update_available": latest["version_code"] > current_version_code,
        "release": latest,
    }


@router.get("/android/history")
def android_release_history(
    channel: str = Query(default="stable", pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$"),
) -> dict[str, Any]:
    return {"releases": android_releases(channel)}


@router.get("/{platform}/history")
def release_history(
    platform: str,
    channel: str = Query(default="stable", pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$"),
) -> dict[str, Any]:
    return {"releases": platform_releases(platform, channel)}
