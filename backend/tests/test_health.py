import pytest
from fastapi.testclient import TestClient

from app.services.store import store


def test_health_endpoint_returns_ok(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_endpoint_reports_database_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_database() -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(store, "check_database", fail_database)

    response = client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
