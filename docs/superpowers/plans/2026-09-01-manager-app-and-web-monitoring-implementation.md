# Relay Hub Manager App and Web Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Build a native read-only Relay Hub Manager Android app and add the same real-time device, message, API, runtime, conflict, and release visibility to the web console.

**Architecture:** FastAPI remains the only source of authentication, message filtering, runtime facts, online state, and conflict warnings. A new in-process admin message broker publishes committed full-message events to authenticated WebSocket subscribers, while REST cursor queries recover missed events. The React console and a new native Kotlin manager app consume the same contracts; the existing collector remains isolated and only adds stable instance/version metadata.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, pytest, React 18, TypeScript, Vitest, Kotlin 2, Android Views, Android Keystore, OkHttp, Gradle 8/AGP 8.7.

---

## Execution Notes

- Follow strict red-green-refactor: every production behavior starts with a failing test.
- Execute inline in the current session; no subagents are authorized for this task.
- Do not create Git commits unless the user explicitly requests them.
- Keep production credentials, administrator tokens, SMS bodies, simulator profiles, ADB logs, screenshots, and Debug APKs out of Git and deployment archives.

## File Map

### Backend

- Create backend/app/services/admin_message_websocket.py: subscription matching and connection broker.
- Create backend/tests/test_admin_message_websocket.py: broker and endpoint behavior.
- Create backend/migrations/versions/20260901_0007_device_runtime_metadata.py: device instance/version columns.
- Modify backend/app/api/relay.py: runtime contract, device serialization, incremental messages, admin WebSocket, event publishing.
- Modify backend/app/api/updates.py: generic release history reads.
- Modify backend/app/core/config.py: safe public runtime settings.
- Modify backend/app/db/models.py: runtime metadata columns.
- Modify backend/app/services/store.py: message cursor query and device runtime updates.
- Modify backend/tests/test_runtime.py, test_relay.py, test_updates.py, test_migrations.py.
- Modify deploy/.env.example, deploy/bootstrap.sh, deploy/bootstrap.ps1, deploy/compose.yml.

### Web

- Create web/src/adminApi.ts: shared types, authenticated fetch and WebSocket URL construction.
- Create web/src/components/LiveMessagePanel.tsx: device/SIM live message UI.
- Create web/src/components/RuntimePanel.tsx: platform/runtime/API information.
- Create web/src/components/ReleaseHistoryPanel.tsx: APK/Root/deploy release history.
- Create matching Vitest files under web/src/components/*.test.tsx.
- Modify web/src/App.tsx, App.test.tsx, App.css.

### Collector

- Create android/app/src/main/java/io/smsrelay/collector/CollectorIdentityStore.kt.
- Create android/app/src/test/kotlin/io/smsrelay/collector/CollectorIdentityStoreTest.kt.
- Modify SmsRelayPayload.kt and SmsRelayProtocolTest.kt.

### Manager Android App

- Create manager-android Gradle project and app module.
- Create model, URL normalization, secure profile storage, API client, WebSocket, state reducer, and Activities under manager-android/app/src/main/java/io/smsrelay/manager/.
- Create unit tests under manager-android/app/src/test/kotlin/io/smsrelay/manager/.
- Create release signing/build scripts that read RELAY_MANAGER_SIGNING_* environment variables.

### Packaging

- Modify deploy/package.ps1 and backend/tests/test_deploy.py.
- Modify release catalog publishing support and local deployment output notes.

## Task 1: Runtime Configuration Contract

**Files:**
- Modify: backend/tests/test_runtime.py
- Modify: backend/tests/test_config.py
- Modify: backend/app/core/config.py
- Modify: backend/app/api/relay.py
- Modify: deploy/.env.example
- Modify: deploy/compose.yml

- [ ] **Step 1: Write failing runtime tests**

Add assertions that authenticated GET /api/v1/admin/runtime returns stable keys without secrets:

    assert payload["platform_version"] == "1.1.0"
    assert payload["public_base_url"] == "https://sms.example.test"
    assert payload["http_port"] == 39300
    assert payload["https_port"] == 39301
    assert payload["app_port"] == 39200
    assert payload["https_enabled"] is True
    assert payload["services"]["app"] == "healthy"
    assert payload["services"]["database"] == "healthy"
    assert "admin_token" not in str(payload).lower()

Add Settings tests that reject invalid ports and normalize a trailing slash in PUBLIC_BASE_URL.

- [ ] **Step 2: Run tests and verify RED**

Run: backend/.venv/Scripts/python.exe -m pytest backend/tests/test_runtime.py backend/tests/test_config.py -q

Expected: FAIL because runtime settings and response keys do not exist.

- [ ] **Step 3: Implement minimal runtime settings and response**

Add fields to Settings:

    platform_version: str = "1.1.0"
    public_base_url: str = ""
    relay_site: str = ":80"
    http_port: int = Field(default=80, ge=1, le=65535)
    https_port: int = Field(default=443, ge=1, le=65535)
    app_port: int = Field(default=8000, ge=1, le=65535)

Return only safe values from runtime_identity. Database health uses SELECT 1 through Store; app health is healthy when the request is served; Caddy is configured/unknown unless an injected health value exists. Never mount the Docker socket.

- [ ] **Step 4: Pass settings through Compose**

Pass PLATFORM_VERSION, PUBLIC_BASE_URL, RELAY_SITE, HTTP_PORT, HTTPS_PORT, and APP_PORT to the app service. Preserve existing .env values in bootstrap scripts.

- [ ] **Step 5: Run tests and verify GREEN**

Run: backend/.venv/Scripts/python.exe -m pytest backend/tests/test_runtime.py backend/tests/test_config.py backend/tests/test_deploy.py -q

Expected: PASS.

## Task 2: Admin Message Subscription Matcher and Broker

**Files:**
- Create: backend/app/services/admin_message_websocket.py
- Create: backend/tests/test_admin_message_websocket.py

- [ ] **Step 1: Write failing matcher tests**

Define desired API:

    subscription = AdminMessageSubscription(device_id="device-1", sim_slot=1)
    assert subscription.matches({"device_id": "device-1", "sim_slot": 1})
    assert not subscription.matches({"device_id": "device-2", "sim_slot": 1})
    assert not subscription.matches({"device_id": "device-1", "sim_slot": 0})

Also test device-only subscriptions, invalid empty device IDs, send timeout removal, one failing socket not blocking another, and duplicate add/remove behavior.

- [ ] **Step 2: Run broker tests and verify RED**

Run: backend/.venv/Scripts/python.exe -m pytest backend/tests/test_admin_message_websocket.py -q

Expected: import failure for admin_message_websocket.

- [ ] **Step 3: Implement minimal broker**

Create:

    @dataclass(frozen=True, slots=True)
    class AdminMessageSubscription:
        device_id: str
        sim_slot: int | None = None
        def matches(self, event: dict[str, Any]) -> bool: ...

    class AdminMessageWebSocketBroker:
        async def add(self, websocket: Any, subscription: AdminMessageSubscription) -> None: ...
        async def remove(self, websocket: Any) -> None: ...
        async def publish(self, event: dict[str, Any]) -> int: ...

Use an asyncio lock only for connection snapshots; perform sends outside the lock with per-socket timeout.

- [ ] **Step 4: Run broker tests and verify GREEN**

Run: backend/.venv/Scripts/python.exe -m pytest backend/tests/test_admin_message_websocket.py -q

Expected: PASS.

## Task 3: Incremental Message REST Contract

**Files:**
- Modify: backend/tests/test_relay.py
- Modify: backend/app/services/store.py
- Modify: backend/app/api/relay.py

- [ ] **Step 1: Write failing cursor tests**

Create three messages with distinct created_at values and assert:

    response = client.get(
        "/api/v1/admin/messages",
        params={"device_id": device_id, "created_after": first_created_at, "after_id": first_id},
        headers=admin_headers,
    )
    assert [item["id"] for item in response.json()] == [second_id, third_id]

Test stable ordering by created_at then ID, invalid timestamp rejection, device/SIM filtering, and a limit maximum of 500.

- [ ] **Step 2: Run the focused test and verify RED**

Run: backend/.venv/Scripts/python.exe -m pytest backend/tests/test_relay.py -k incremental_admin_messages -q

Expected: FAIL because created_after and after_id are unsupported.

- [ ] **Step 3: Implement cursor filtering**

Extend Store.list_messages and the route with created_after and after_id. Use:

    or_(
        MessageModel.created_at > created_after,
        and_(MessageModel.created_at == created_after, MessageModel.id > after_id),
    )

Return ascending order for cursor recovery and preserve current newest-first behavior when no cursor is supplied.

- [ ] **Step 4: Run focused and relay tests**

Run: backend/.venv/Scripts/python.exe -m pytest backend/tests/test_relay.py -q

Expected: PASS.

## Task 4: Authenticated Full-Message WebSocket

**Files:**
- Modify: backend/tests/test_admin_message_websocket.py
- Modify: backend/app/api/relay.py
- Modify: backend/app/services/admin_message_websocket.py

- [ ] **Step 1: Write failing endpoint tests**

Test this frame sequence:

    websocket.send_json({"type": "auth", "token": admin_token})
    assert websocket.receive_json()["type"] == "auth_ok"
    websocket.send_json({"type": "subscribe", "device_id": device_id, "sim_slot": 1})
    assert websocket.receive_json()["type"] == "subscribed"

After HTTP ingest commits, assert one message event containing full body, sender, OTP code, device ID, SIM slot, created_at, and cursor. Test invalid token close, inactive device subscription rejection, nonmatching device suppression, duplicate ingest suppression, ping/pong, and logout/session invalidation.

- [ ] **Step 2: Run endpoint tests and verify RED**

Run: backend/.venv/Scripts/python.exe -m pytest backend/tests/test_admin_message_websocket.py -q

Expected: 404 or disconnect because the endpoint is missing.

- [ ] **Step 3: Implement endpoint and publish after commit**

Add @router.websocket("/ws/admin/messages"). Authenticate the first frame using Store validation, require an explicit device ID, add the socket to the broker, and remove it in finally.

Publish only when ingest reports duplicate=false. Serialize the committed message with the same _serialize_message function used by REST and add:

    event["type"] = "message"
    event["cursor"] = {"created_at": event["created_at"], "id": event["id"]}

- [ ] **Step 4: Run endpoint and regression tests**

Run: backend/.venv/Scripts/python.exe -m pytest backend/tests/test_admin_message_websocket.py backend/tests/test_otp_websocket.py backend/tests/test_relay.py -q

Expected: PASS.

## Task 5: Device Runtime Metadata and Conflict Warnings

**Files:**
- Create: backend/migrations/versions/20260901_0007_device_runtime_metadata.py
- Modify: backend/app/db/models.py
- Modify: backend/app/services/store.py
- Modify: backend/app/api/relay.py
- Modify: backend/tests/test_migrations.py
- Modify: backend/tests/test_relay.py

- [ ] **Step 1: Write failing migration and behavior tests**

Assert the devices table gains nullable client_instance_id, client_version, client_mode, last_source_fingerprint, and runtime_updated_at columns. Ingest metadata:

    {"collector_instance_id": "instance-a", "collector_version": "1.0.12", "collector_mode": "android_app"}

Then assert list_devices returns online=true and no warning. Ingest with instance-b using the same device inside the warning window and assert credential_reuse_warning=true. Ingest matching message features from android_app and root for one device/SIM and assert duplicate_collection_warning=true.

- [ ] **Step 2: Run tests and verify RED**

Run: backend/.venv/Scripts/python.exe -m pytest backend/tests/test_migrations.py backend/tests/test_relay.py -k "runtime_metadata or conflict_warning" -q

Expected: FAIL because columns and response fields are absent.

- [ ] **Step 3: Implement schema and conservative warnings**

Persist the latest instance/version/mode and a keyed hash of source address, never raw IP. Compute online from last_seen_at within a configurable 180-second window. Keep warnings read-only and do not reject ingest.

- [ ] **Step 4: Run migration and relay tests**

Run: backend/.venv/Scripts/python.exe -m pytest backend/tests/test_migrations.py backend/tests/test_relay.py -q

Expected: PASS.

## Task 6: Generic Release History Reads

**Files:**
- Modify: backend/tests/test_updates.py
- Modify: backend/app/api/updates.py
- Modify: backend/app/services/release_catalog.py

- [ ] **Step 1: Write failing platform history tests**

Create a catalog with android, root, and deploy entries and assert:

    client.get("/api/v1/releases/root/history?channel=stable").json()["releases"][0]["version_name"] == "0.3.7"
    client.get("/api/v1/releases/deploy/history?channel=stable").json()["releases"][0]["version_name"] == "2026.09.01"

Reject unsupported platform values and malformed catalogs.

- [ ] **Step 2: Run and verify RED**

Run: backend/.venv/Scripts/python.exe -m pytest backend/tests/test_updates.py -q

Expected: 404 for root/deploy routes.

- [ ] **Step 3: Implement read-only generic routes**

Add GET /api/v1/releases/{platform}/history with an allowlist of android, root, and deploy. Keep Android latest compatibility unchanged.

- [ ] **Step 4: Run and verify GREEN**

Run: backend/.venv/Scripts/python.exe -m pytest backend/tests/test_updates.py -q

Expected: PASS.

## Task 7: Web Shared API Layer

**Files:**
- Create: web/src/adminApi.ts
- Create: web/src/adminApi.test.ts
- Modify: web/src/App.tsx

- [ ] **Step 1: Write failing API helper tests**

Test authenticated JSON requests, stable error code parsing, Base URL to WS/WSS conversion, and cursor query encoding:

    expect(adminWebSocketUrl("https://sms.test", "/api/v1/ws/admin/messages"))
      .toBe("wss://sms.test/api/v1/ws/admin/messages")

- [ ] **Step 2: Run and verify RED**

Run: npm test -- adminApi.test.ts

Expected: module not found.

- [ ] **Step 3: Implement types and helpers**

Define AdminDevice, AdminMessage, RuntimeInfo, ReleaseEntry, MessageCursor, adminRequest, adminWebSocketUrl, and messageCursorQuery. Move duplicate request logic out of App.tsx without changing behavior.

- [ ] **Step 4: Run helper and existing App tests**

Run: npm test -- adminApi.test.ts App.test.tsx

Expected: PASS.

## Task 8: Web Live Message Panel

**Files:**
- Create: web/src/components/LiveMessagePanel.tsx
- Create: web/src/components/LiveMessagePanel.test.tsx
- Modify: web/src/App.tsx
- Modify: web/src/App.css

- [ ] **Step 1: Write failing component tests**

Render device and SIM selectors, feed auth_ok/subscribed/message frames through a fake WebSocket, and assert full body plus OTP code render once. Test reconnect status, cursor recovery callback, switching device clears old messages, and copy buttons.

- [ ] **Step 2: Run and verify RED**

Run: npm test -- LiveMessagePanel.test.tsx

Expected: component missing.

- [ ] **Step 3: Implement minimal panel**

The component receives baseUrl, token, devices, selectedDeviceId, selectedSimSlot, and onUnauthorized. Maintain a Map keyed by message ID, cap visible live messages at 500, and reconnect with bounded delays of 1, 2, 5, 10, and 30 seconds.

- [ ] **Step 4: Run component and App tests**

Run: npm test -- LiveMessagePanel.test.tsx App.test.tsx

Expected: PASS.

## Task 9: Web Runtime and Release Panels

**Files:**
- Create: web/src/components/RuntimePanel.tsx
- Create: web/src/components/RuntimePanel.test.tsx
- Create: web/src/components/ReleaseHistoryPanel.tsx
- Create: web/src/components/ReleaseHistoryPanel.test.tsx
- Modify: web/src/App.tsx
- Modify: web/src/App.css

- [ ] **Step 1: Write failing panel tests**

Assert runtime values, unencrypted HTTP warning, certificate expiry, service health, API/WS copy targets, true online device counts, conflict badges, and android/root/deploy history render from API fixtures.

- [ ] **Step 2: Run and verify RED**

Run: npm test -- RuntimePanel.test.tsx ReleaseHistoryPanel.test.tsx

Expected: components missing.

- [ ] **Step 3: Implement panels and App navigation**

Add a Platform tab containing RuntimePanel and ReleaseHistoryPanel. Replace the label 在线设备 based on active=true with 已启用设备, and add a separately computed true online count from device.online.

- [ ] **Step 4: Run full web tests and build**

Run: npm test && npm run typecheck && npm run build

Expected: all commands exit 0.

## Task 10: Collector Stable Instance and Version Metadata

**Files:**
- Create: android/app/src/main/java/io/smsrelay/collector/CollectorIdentityStore.kt
- Create: android/app/src/test/kotlin/io/smsrelay/collector/CollectorIdentityStoreTest.kt
- Modify: android/app/src/main/java/io/smsrelay/collector/SmsRelayPayload.kt
- Modify: android/app/src/test/kotlin/io/smsrelay/collector/SmsRelayProtocolTest.kt

- [ ] **Step 1: Write failing identity tests**

Use an in-memory key-value adapter and assert one UUID is generated once, persists across reads, and can be cleared only explicitly. Assert payload metadata contains collector_instance_id, collector_version from BuildConfig.VERSION_NAME, and collector_mode=android_app.

- [ ] **Step 2: Run and verify RED**

Run: gradle -p android testDebugUnitTest --tests "io.smsrelay.collector.CollectorIdentityStoreTest" --no-daemon

Expected: class missing.

- [ ] **Step 3: Implement identity store and payload metadata injection**

Store a random UUID in private SharedPreferences. Pass instance ID and version into SmsRelayPayload.fromSms from SmsReceivedReceiver; do not include hardware IDs.

- [ ] **Step 4: Run Android collector tests**

Run: gradle -p android testDebugUnitTest --no-daemon

Expected: PASS.

## Task 11: Manager Android Project and Core Models

**Files:**
- Create: manager-android/settings.gradle.kts
- Create: manager-android/build.gradle.kts
- Create: manager-android/gradle.properties
- Create: manager-android/app/build.gradle.kts
- Create: manager-android/app/src/main/AndroidManifest.xml
- Create: manager-android/app/src/main/java/io/smsrelay/manager/model/Models.kt
- Create: manager-android/app/src/main/java/io/smsrelay/manager/net/BaseUrl.kt
- Create: manager-android/app/src/test/kotlin/io/smsrelay/manager/net/BaseUrlTest.kt

- [ ] **Step 1: Write failing URL/model tests**

Assert normalization:

    assertEquals("https://sms.example.com", BaseUrl.parse("https://sms.example.com/").value)
    assertEquals("http://203.0.113.10:39300", BaseUrl.parse("http://203.0.113.10:39300").value)
    assertFails { BaseUrl.parse("ftp://example.com") }
    assertFails { BaseUrl.parse("https://user:pass@example.com") }

- [ ] **Step 2: Run and verify RED**

Run: gradle -p manager-android testDebugUnitTest --no-daemon

Expected: project or BaseUrl class missing.

- [ ] **Step 3: Scaffold minimal project and models**

Use applicationId io.smsrelay.manager, minSdk 26, targetSdk 35, Kotlin JVM 17, AndroidX core/appcompat/recyclerview/lifecycle, coroutines, OkHttp, and org.json. Define PlatformProfile, AdminSession, AdminDevice, SimProfile, AdminMessage, RuntimeInfo, ReleaseEntry, and ConnectionState.

- [ ] **Step 4: Run and verify GREEN**

Run: gradle -p manager-android testDebugUnitTest --no-daemon

Expected: PASS.

## Task 12: Secure Multi-Platform Profile Store

**Files:**
- Create: manager-android/app/src/main/java/io/smsrelay/manager/security/SecretCipher.kt
- Create: manager-android/app/src/main/java/io/smsrelay/manager/profile/PlatformProfileStore.kt
- Create: manager-android/app/src/test/kotlin/io/smsrelay/manager/profile/PlatformProfileStoreTest.kt

- [ ] **Step 1: Write failing store tests**

Test add/update/delete, multiple profiles, selected profile, username persistence, password omitted when rememberPassword=false, encrypted password/session round trip, and deletion clearing secrets. Inject a fake cipher in unit tests so tests verify storage behavior without Android Keystore.

- [ ] **Step 2: Run and verify RED**

Run: gradle -p manager-android testDebugUnitTest --tests "io.smsrelay.manager.profile.PlatformProfileStoreTest" --no-daemon

Expected: store missing.

- [ ] **Step 3: Implement store and production Keystore cipher**

Use AES/GCM/NoPadding with a non-exportable AndroidKeyStore key. Persist only ciphertext and IV; never log values. A profile deletion must delete all ciphertext entries for that profile.

- [ ] **Step 4: Run and verify GREEN**

Run: gradle -p manager-android testDebugUnitTest --no-daemon

Expected: PASS.

## Task 13: Manager Admin API and Message State

**Files:**
- Create: manager-android/app/src/main/java/io/smsrelay/manager/net/RelayAdminClient.kt
- Create: manager-android/app/src/main/java/io/smsrelay/manager/net/AdminMessageSocket.kt
- Create: manager-android/app/src/main/java/io/smsrelay/manager/state/MessageTimeline.kt
- Create: matching unit tests under manager-android/app/src/test/kotlin/io/smsrelay/manager/.

- [ ] **Step 1: Write failing client and reducer tests**

Using MockWebServer, test login, runtime, devices, SIM profiles, cursor messages, release history, 401 mapping, invalid JSON, HTTP warning, and no authorization leakage to another origin. Test MessageTimeline deduplication, newest-first order, 500-item cap, cursor calculation, and clearing on platform/device switch.

- [ ] **Step 2: Run and verify RED**

Run: gradle -p manager-android testDebugUnitTest --no-daemon

Expected: client/socket/timeline missing.

- [ ] **Step 3: Implement minimal client, parsers and socket**

All admin requests use X-Admin-Token. WebSocket sends auth then subscribe frames, exposes typed state callbacks, and never retries after an authentication failure. Network failures use bounded reconnect delay and trigger REST cursor recovery before resubscription.

- [ ] **Step 4: Run and verify GREEN**

Run: gradle -p manager-android testDebugUnitTest --no-daemon

Expected: PASS.

## Task 14: Manager Native UI

**Files:**
- Create: manager-android/app/src/main/java/io/smsrelay/manager/MainActivity.kt
- Create: manager-android/app/src/main/java/io/smsrelay/manager/ui/PlatformScreen.kt
- Create: manager-android/app/src/main/java/io/smsrelay/manager/ui/DashboardScreen.kt
- Create: manager-android/app/src/main/java/io/smsrelay/manager/ui/MessageScreen.kt
- Create: manager-android/app/src/main/java/io/smsrelay/manager/ui/DeviceScreen.kt
- Create: manager-android/app/src/main/java/io/smsrelay/manager/ui/ApiScreen.kt
- Create: manager-android/app/src/main/res/values/strings.xml
- Create: manager-android/app/src/main/res/values/themes.xml
- Create: manager-android/app/src/androidTest/java/io/smsrelay/manager/ManagerSmokeTest.kt

- [ ] **Step 1: Write failing UI smoke test**

Launch MainActivity and assert platform address, username, password, remember-password toggle, test-connection, and login controls exist. After injecting a fake authenticated repository, assert dashboard, messages, devices/SIM, and API tabs exist, and message rows show both full body and OTP.

- [ ] **Step 2: Run and verify RED**

Run: gradle -p manager-android connectedDebugAndroidTest --no-daemon

Expected: activity/screens missing.

- [ ] **Step 3: Implement read-only native screens**

Use one Activity with focused screen classes and bottom navigation. Require device selection before opening live messages. Show a persistent warning for HTTP profiles. Copy actions use ClipboardManager. No destructive management controls are present.

- [ ] **Step 4: Run unit and connected tests**

Run: gradle -p manager-android testDebugUnitTest connectedDebugAndroidTest lintDebug --no-daemon

Expected: PASS with no lint errors.

## Task 15: Deployment Packaging and Release Builds

**Files:**
- Modify: deploy/package.ps1
- Modify: backend/tests/test_deploy.py
- Create: manager-android/build-release.ps1
- Create: manager-android/setup-release-signing.ps1
- Modify: release catalog assets and local deployment notes.

- [ ] **Step 1: Write failing packaging tests**

Assert the deployment ZIP includes new backend/web code and release catalog entries, excludes manager source/build directories, .env, keystores, signing properties, databases, logs, screenshots, Debug APKs, and temporary files. Assert a published manager APK record contains size, SHA-256, signer SHA-256, version code/name, notes, and same-origin download URL.

- [ ] **Step 2: Run and verify RED**

Run: backend/.venv/Scripts/python.exe -m pytest backend/tests/test_deploy.py backend/tests/test_updates.py -q

Expected: FAIL because manager release and exclusions are not represented.

- [ ] **Step 3: Implement signing and packaging**

Manager release signing reads RELAY_MANAGER_SIGNING_STORE_FILE, RELAY_MANAGER_SIGNING_STORE_PASSWORD, RELAY_MANAGER_SIGNING_KEY_ALIAS, and RELAY_MANAGER_SIGNING_KEY_PASSWORD. Store the actual keystore outside the repository. Build and publish Relay-Hub-Manager-1.0.0.apk, update history, and generate the deployment ZIP.

- [ ] **Step 4: Run complete automated verification**

Run:

    backend/.venv/Scripts/python.exe -m pytest backend/tests -q
    npm test --prefix web
    npm run typecheck --prefix web
    npm run build --prefix web
    gradle -p android testDebugUnitTest lintDebug assembleRelease --no-daemon
    gradle -p manager-android testDebugUnitTest lintDebug assembleRelease --no-daemon
    pwsh -NoProfile -File deploy/package.ps1

Expected: every command exits 0.

- [ ] **Step 5: Temporary emulator acceptance**

Install the manager Debug/Release APK in the current LDPlayer instance, test one HTTP platform and one HTTPS platform, save/switch multiple profiles, login, select device/SIM, receive a controlled live full SMS and OTP, disconnect/reconnect network, and verify cursor recovery without duplicates.

- [ ] **Step 6: Clean temporary test artifacts**

Clear saved profiles and sessions in the emulator, uninstall manager Debug APK, and delete ADB logs, screenshots, temporary responses, JSON files, test databases, Debug APKs, temporary certificates, and test server configuration. Keep source tests, Release APKs, release catalogs, checksums, and anonymized acceptance results.

- [ ] **Step 7: Produce final local delivery**

Copy the signed manager APK and deployment ZIP to F:/jd/私人学习项目1/部署文件, retain versioned history, calculate SHA-256, and update LATEST.txt with exact filenames and deployment status.
