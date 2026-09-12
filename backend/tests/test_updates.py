import json
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.core.config import settings


def write_catalog(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "releases": [
                    {
                        "platform": "android",
                        "channel": "stable",
                        "version_code": 9,
                        "version_name": "1.0.8",
                        "published_at": "2026-08-23T09:41:56Z",
                        "min_sdk": 26,
                        "mandatory": False,
                        "notes": "Current release",
                        "size_bytes": 2965657,
                        "sha256": "6c36d2b1a9b6f14150bf7cb3f2c3a1aa2304cf868acc03353ad8b8195ccab333",
                        "signer_sha256": "f6bfe7a63a07950244cc0a8e21f551bdce9cd5bd375c22876de2d97eaaab27ff",
                        "download_url": "/assets/downloads/Relay-SMS-Forwarder.apk",
                    },
                    {
                        "platform": "android",
                        "channel": "stable",
                        "version_code": 10,
                        "version_name": "1.0.9",
                        "published_at": "2026-09-01T08:00:00Z",
                        "min_sdk": 26,
                        "mandatory": False,
                        "notes": "Safe online updater",
                        "size_bytes": 3000000,
                        "sha256": "a" * 64,
                        "signer_sha256": "f6bfe7a63a07950244cc0a8e21f551bdce9cd5bd375c22876de2d97eaaab27ff",
                        "download_url": "/assets/releases/android/10/Relay-SMS-Forwarder-1.0.9.apk",
                    },
                    {
                        "platform": "root",
                        "channel": "stable",
                        "version_code": 307,
                        "version_name": "0.3.7",
                        "published_at": "2026-09-01T09:00:00Z",
                        "min_sdk": 21,
                        "mandatory": False,
                        "notes": "Root module stable release",
                        "size_bytes": 1200000,
                        "sha256": "b" * 64,
                        "signer_sha256": None,
                        "download_url": "/assets/releases/root/307/relay-root-0.3.7.zip",
                    },
                    {
                        "platform": "manager",
                        "channel": "stable",
                        "version_code": 1,
                        "version_name": "1.0.0",
                        "published_at": "2026-09-01T10:30:00Z",
                        "min_sdk": 26,
                        "mandatory": False,
                        "notes": "Native Relay Hub management app",
                        "size_bytes": 4200000,
                        "sha256": "f" * 64,
                        "signer_sha256": "1" * 64,
                        "download_url": "/assets/releases/manager/1/Relay-Hub-Manager-1.0.0.apk",
                    },
                    {
                        "platform": "deploy",
                        "channel": "stable",
                        "version_code": 20260901,
                        "version_name": "2026.09.01",
                        "published_at": "2026-09-01T10:00:00Z",
                        "min_sdk": 21,
                        "mandatory": False,
                        "notes": "Backend and web deployment package",
                        "size_bytes": 38000000,
                        "sha256": "d" * 64,
                        "signer_sha256": None,
                        "download_url": "/assets/releases/deploy/20260901/sms-relay-hub-deploy.zip",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_latest_android_release_reports_only_strictly_newer_versions(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    catalog = tmp_path / "release-catalog.json"
    write_catalog(catalog)
    monkeypatch.setattr(settings, "release_manifest_path", str(catalog))

    available = client.get(
        "/api/v1/releases/android/latest",
        params={"channel": "stable", "current_version_code": 9},
    )
    current = client.get(
        "/api/v1/releases/android/latest",
        params={"channel": "stable", "current_version_code": 10},
    )

    assert available.status_code == 200
    assert available.json()["update_available"] is True
    assert available.json()["release"]["version_code"] == 10
    assert current.status_code == 200
    assert current.json()["update_available"] is False
    assert current.json()["release"]["version_code"] == 10


def test_android_release_history_is_newest_first(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    catalog = tmp_path / "release-catalog.json"
    write_catalog(catalog)
    monkeypatch.setattr(settings, "release_manifest_path", str(catalog))

    response = client.get("/api/v1/releases/android/history", params={"channel": "stable"})

    assert response.status_code == 200
    assert [item["version_code"] for item in response.json()["releases"]] == [10, 9]


def test_generic_release_history_supports_manager_root_and_deploy(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    catalog = tmp_path / "release-catalog.json"
    write_catalog(catalog)
    monkeypatch.setattr(settings, "release_manifest_path", str(catalog))

    root = client.get("/api/v1/releases/root/history", params={"channel": "stable"})
    manager = client.get("/api/v1/releases/manager/history", params={"channel": "stable"})
    deploy = client.get("/api/v1/releases/deploy/history", params={"channel": "stable"})

    assert root.status_code == 200
    assert root.json()["releases"][0]["version_name"] == "0.3.7"
    assert manager.status_code == 200
    assert manager.json()["releases"][0]["version_name"] == "1.0.0"
    assert deploy.status_code == 200
    assert deploy.json()["releases"][0]["version_name"] == "2026.09.01"


def test_generic_release_history_rejects_unsupported_platform(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/releases/ios/history")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "release_platform_not_supported"


def test_release_catalog_rejects_cross_origin_downloads(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    catalog = tmp_path / "release-catalog.json"
    write_catalog(catalog)
    payload = json.loads(catalog.read_text(encoding="utf-8"))
    payload["releases"][1]["download_url"] = "https://attacker.invalid/update.apk"
    catalog.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(settings, "release_manifest_path", str(catalog))

    response = client.get(
        "/api/v1/releases/android/latest",
        params={"channel": "stable", "current_version_code": 9},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "release_catalog_invalid"


def test_release_catalog_requires_signer_for_apk_artifacts(
    client: TestClient,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    catalog = tmp_path / "release-catalog.json"
    write_catalog(catalog)
    payload = json.loads(catalog.read_text(encoding="utf-8"))
    payload["releases"][1]["signer_sha256"] = None
    catalog.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(settings, "release_manifest_path", str(catalog))

    response = client.get("/api/v1/releases/android/history")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "release_catalog_invalid"
