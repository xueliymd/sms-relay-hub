import json

from fastapi.testclient import TestClient

ADMIN_HEADERS = {"X-Admin-Token": "neutral-admin-token-for-tests-123456"}


def test_android_device_enrollment_qr_is_short_lived_and_single_use(client: TestClient) -> None:
    created = client.post(
        "/api/v1/admin/devices",
        headers=ADMIN_HEADERS,
        json={"name": "QR Android", "client_type": "android_app", "auth_kind": "hmac"},
    )
    assert created.status_code == 201
    created_payload = created.json()
    device_id = created_payload["device"]["id"]
    secret = created_payload["credential"]["secret"]

    qr_response = client.post(
        f"/api/v1/admin/devices/{device_id}/enrollment-qr",
        headers=ADMIN_HEADERS,
    )
    assert qr_response.status_code == 200
    qr_payload = qr_response.json()
    qr_text = json.loads(qr_payload["qr_text"])
    assert qr_text["type"] == "sms-relay-enroll"
    assert qr_text["device_id"] == device_id
    assert secret not in qr_payload["qr_text"]
    assert qr_payload["ttl_seconds"] == 300

    consumed = client.post(
        "/api/v1/enrollment/consume",
        json={"device_id": device_id, "enrollment_token": qr_text["token"]},
    )
    assert consumed.status_code == 200
    assert consumed.json() == {
        "device_id": device_id,
        "device_name": "QR Android",
        "client_type": "android_app",
        "auth_kind": "hmac",
        "hmac_secret": secret,
    }

    replay = client.post(
        "/api/v1/enrollment/consume",
        json={"device_id": device_id, "enrollment_token": qr_text["token"]},
    )
    assert replay.status_code == 400
    assert replay.json()["error"]["code"] == "invalid_enrollment_token"


def test_root_module_device_enrollment_qr_returns_root_hmac_credential(client: TestClient) -> None:
    created = client.post(
        "/api/v1/admin/devices",
        headers=ADMIN_HEADERS,
        json={"name": "QR Root", "client_type": "root_module", "auth_kind": "hmac"},
    )
    assert created.status_code == 201, created.text
    device_id = created.json()["device"]["id"]

    response = client.post(f"/api/v1/admin/devices/{device_id}/enrollment-qr", headers=ADMIN_HEADERS)
    assert response.status_code == 200, response.text
    qr_text = json.loads(response.json()["qr_text"])
    assert qr_text["type"] == "sms-relay-enroll"

    consumed = client.post(
        "/api/v1/enrollment/consume",
        json={"device_id": device_id, "enrollment_token": qr_text["token"]},
    )
    assert consumed.status_code == 200, consumed.text
    assert consumed.json()["client_type"] == "root_module"
    assert consumed.json()["auth_kind"] == "hmac"
    assert len(consumed.json()["hmac_secret"]) >= 32


def test_only_android_hmac_devices_can_create_enrollment_qr(client: TestClient) -> None:
    created = client.post(
        "/api/v1/admin/devices",
        headers=ADMIN_HEADERS,
        json={"name": "Shortcut", "client_type": "ios_shortcut", "auth_kind": "bearer"},
    )
    device_id = created.json()["device"]["id"]

    response = client.post(
        f"/api/v1/admin/devices/{device_id}/enrollment-qr",
        headers=ADMIN_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "device_enrollment_unsupported"


def test_enrollment_token_is_removed_when_device_credential_rotates(client: TestClient) -> None:
    created = client.post(
        "/api/v1/admin/devices",
        headers=ADMIN_HEADERS,
        json={"name": "Rotating QR Android", "client_type": "android_app", "auth_kind": "hmac"},
    )
    device_id = created.json()["device"]["id"]
    qr_response = client.post(
        f"/api/v1/admin/devices/{device_id}/enrollment-qr",
        headers=ADMIN_HEADERS,
    )
    token = json.loads(qr_response.json()["qr_text"])["token"]

    rotated = client.post(
        f"/api/v1/admin/devices/{device_id}/rotate-credential",
        headers=ADMIN_HEADERS,
    )
    assert rotated.status_code == 200

    response = client.post(
        "/api/v1/enrollment/consume",
        json={"device_id": device_id, "enrollment_token": token},
    )
    assert response.status_code == 400
