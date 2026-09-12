from typing import cast

from fastapi.testclient import TestClient
from httpx import Response

RECOVERY_HEADERS = {"X-Admin-Token": "neutral-admin-token-for-tests-123456"}


def login(
    client: TestClient,
    username: str = "admin",
    password: str = "neutral-admin-password-for-tests-123456",
) -> Response:
    return cast(
        Response,
        client.post(
            "/api/v1/admin/login",
            json={"username": username, "password": password},
        ),
    )


def test_username_password_login_returns_session_accepted_by_admin_api(
    client: TestClient,
) -> None:
    response = login(client)

    assert response.status_code == 200
    payload = response.json()
    assert payload["username"] == "admin"
    assert payload["token"]
    assert payload["expires_at"]

    devices = client.get(
        "/api/v1/admin/devices",
        headers={"X-Admin-Token": payload["token"]},
    )
    assert devices.status_code == 200


def test_invalid_password_is_rejected_and_recovery_token_remains_valid(
    client: TestClient,
) -> None:
    denied = login(client, password="wrong-password")
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "admin_auth_required"

    recovered = client.get("/api/v1/admin/devices", headers=RECOVERY_HEADERS)
    assert recovered.status_code == 200


def test_admin_can_change_username_and_password_and_old_session_is_invalidated(
    client: TestClient,
) -> None:
    old_login = login(client)
    old_token = old_login.json()["token"]

    updated = client.put(
        "/api/v1/admin/account",
        headers={"X-Admin-Token": old_token},
        json={
            "current_password": "neutral-admin-password-for-tests-123456",
            "username": "relay-admin",
            "password": "new-neutral-admin-password-123456",
        },
    )

    assert updated.status_code == 200
    assert updated.json()["username"] == "relay-admin"
    new_token = updated.json()["token"]

    old_session = client.get(
        "/api/v1/admin/devices",
        headers={"X-Admin-Token": old_token},
    )
    assert old_session.status_code == 401

    old_credentials = login(client)
    assert old_credentials.status_code == 401

    new_credentials = login(
        client,
        username="relay-admin",
        password="new-neutral-admin-password-123456",
    )
    assert new_credentials.status_code == 200
    assert new_credentials.json()["token"]

    new_session = client.get(
        "/api/v1/admin/devices",
        headers={"X-Admin-Token": new_token},
    )
    assert new_session.status_code == 200
