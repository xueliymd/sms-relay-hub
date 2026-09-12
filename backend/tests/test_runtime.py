import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.services.store import store


def test_database_identity_is_available_only_to_an_authenticated_admin(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "platform_version", "1.1.0")
    monkeypatch.setattr(settings, "public_base_url", "https://sms.example.test")
    monkeypatch.setattr(settings, "relay_site", "sms.example.test")
    monkeypatch.setattr(settings, "http_port", 39300)
    monkeypatch.setattr(settings, "https_port", 39301)
    monkeypatch.setattr(settings, "app_port", 39200)
    unauthenticated = client.get("/api/v1/admin/runtime")
    assert unauthenticated.status_code == 401

    response = client.get(
        "/api/v1/admin/runtime",
        headers={"X-Admin-Token": settings.admin_token},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["database_backend"] == "sqlite"
    assert len(payload["database_fingerprint"]) == 12
    assert payload["platform_version"] == "1.1.0"
    assert payload["public_base_url"] == "https://sms.example.test"
    assert payload["http_port"] == 39300
    assert payload["https_port"] == 39301
    assert payload["app_port"] == 39200
    assert payload["https_enabled"] is True
    assert payload["services"] == {
        "app": "healthy",
        "database": "healthy",
        "caddy": "configured",
    }
    assert payload["server_time"].endswith("+00:00")
    # TLS 由上游反代终结，容器内不持有证书 → 字段在但恒为 null（区分"不适用"与"漏返回"）
    assert payload["certificate_expires_at"] is None
    assert isinstance(payload["latest_releases"], dict)
    assert "database_url" not in payload
    assert "admin_token" not in str(payload).lower()
    assert "data_encryption_key" not in str(payload).lower()


def test_runtime_reports_latest_stable_release_per_platform(client: TestClient) -> None:
    """设计文档要求 runtime 返回最新 APK/Root/部署包版本——直接读发布清单，避免手写副本漂移。"""
    response = client.get(
        "/api/v1/admin/runtime",
        headers={"X-Admin-Token": settings.admin_token},
    )
    assert response.status_code == 200
    latest = response.json()["latest_releases"]

    # 发布清单里 android / manager 一定有 stable 条目，且取的是各平台 version_code 最大者。
    assert latest["android"]["version_code"] > 0
    assert latest["android"]["version_name"].startswith("1.")
    assert latest["manager"]["version_code"] > 0
    assert latest["android"]["download_url"].startswith("/assets/releases/android/")
    assert latest["android"]["sha256"] == latest["android"]["sha256"].lower()
    assert len(latest["android"]["sha256"]) == 64
    # 只暴露展示所需字段，不整条透传清单。
    assert set(latest["android"]) == {
        "version_code",
        "version_name",
        "published_at",
        "download_url",
        "sha256",
    }


def test_database_fingerprint_ignores_password_rotation() -> None:
    original_url = store.database_url
    try:
        store.database_url = "postgresql+psycopg://relay:first-secret@db:5432/messages"
        first = store.database_fingerprint()
        store.database_url = "postgresql+psycopg://relay:second-secret@db:5432/messages"
        second = store.database_fingerprint()
        store.database_url = "postgresql+psycopg://relay:second-secret@db:5432/other"
        other_database = store.database_fingerprint()
    finally:
        store.database_url = original_url

    assert first == second
    assert first != other_database
