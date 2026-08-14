# Android Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 1 non-root Android collector that receives real SMS broadcasts, persists an upload queue, signs ingest requests with a Keystore-backed HMAC credential, retries safely, and exposes enrollment, status, and local history screens for private APK distribution.

**Architecture:** A single native Android application lives under `android/` and uses a manual application container instead of a dependency-injection framework. `SMS_RECEIVED` is converted into a deterministic protocol message and written to Room before any network work; a unique WorkManager job drains that queue through an OkHttp client, redacts local sensitive fields only after server acknowledgement, and surfaces terminal auth/conflict states to Compose. Non-secret configuration is stored in DataStore, while the server-issued HMAC key is imported into Android Keystore and is never written to source, Gradle properties, logs, saved state, or application preferences.

**Tech Stack:** Kotlin 2.0.21, Android Gradle Plugin 8.7.3, Gradle 8.9, Java 17, compile/target SDK 35, min SDK 26, Jetpack Compose Material 3, Room 2.6.1, WorkManager 2.10.0, DataStore 1.1.1, kotlinx.serialization 1.7.3, OkHttp 4.12.0, Android Keystore, JUnit 4, Robolectric, MockWebServer, AndroidX test, Compose UI test.

---

## Scope And Locked Contracts

- The Android project root is `android/`. Android production code and Android component tests do not leave that directory.
- Shared protocol vectors and live Android-to-backend checks live under `tests/`. Temporary request captures, logcat exports, APK inspection output, and local certificates live under `debug/` and are never committed.
- The package and application ID are `io.smsrelay.collector`.
- The app accepts credentials created by `POST /api/v1/admin/devices` and rotated by `POST /api/v1/admin/devices/{id}/rotate-credential`; it does not call either administrator endpoint itself.
- Enrollment is manual entry of HTTPS base URL, device ID, and the one-time base64url HMAC secret shown by the Web administrator UI.
- Android authenticates `POST /api/v1/ingest/messages` with HMAC headers only. It sends no `Authorization` header.
- `X-Timestamp` is Unix seconds in UTC.
- `X-Nonce` is 16 random bytes encoded as base64url without padding.
- The SHA-256 body digest is lowercase hexadecimal over the exact UTF-8 request bytes.
- The canonical HMAC input is `timestamp + "\n" + nonce + "\n" + bodySha256Hex`.
- `X-Signature` is HMAC-SHA256 output encoded as base64url without padding.
- `X-Idempotency-Key` equals `client_message_id`.
- First acceptance is HTTP 201 with `{ "accepted": true, "duplicate": false, "message_id": "<uuid>" }`.
- An exact idempotent replay is HTTP 200 with `{ "accepted": true, "duplicate": true, "message_id": "<same uuid>" }`.
- HTTP 201 and the duplicate HTTP 200 are the only successful ingest outcomes.
- HTTP 409 means the same idempotency key was reused with different request bytes. It is a terminal local `CONFLICT` state and is never retried.
- HTTP 401/403 block automatic upload until the operator installs a new credential.
- HTTP 429, HTTP 5xx, and network I/O failures are retried with WorkManager exponential backoff whose initial delay is randomized from 30 through 45 seconds.
- `sim_slot` is the zero-based Android subscription slot index. The UI renders slot `0` as `SIM 1`; unknown slot and subscription values are omitted from JSON.
- `received_at` is RFC 3339 UTC generated from the SMS timestamp, for example `2026-08-14T02:23:48Z`.
- The local queue holds at most 500 message records. On overflow, the oldest local record is removed so the newest SMS is retained, and the dropped-record count is shown in status.
- Successful records keep only client ID, timestamps, status, SIM metadata, and server message ID; sender and body are set to SQL `NULL` in the same Room update that marks the record uploaded.
- Phase 1 does not include Root, Magisk, KernelSU, APatch, notification scraping, iOS code, public Play Store publication, remote log collection, or a background foreground service.

## File Structure

```text
android/
├── README.md
├── build.gradle.kts
├── gradle.properties
├── settings.gradle.kts
├── gradle/
│   ├── libs.versions.toml
│   └── wrapper/gradle-wrapper.properties
├── app/
│   ├── build.gradle.kts
│   ├── proguard-rules.pro
│   ├── schemas/
│   └── src/
│       ├── main/
│       │   ├── AndroidManifest.xml
│       │   └── java/io/smsrelay/collector/
│       │       ├── CollectorApplication.kt
│       │       ├── MainActivity.kt
│       │       ├── app/AppContainer.kt
│       │       ├── config/DeviceConfig.kt
│       │       ├── config/DeviceConfigStore.kt
│       │       ├── config/EnrollmentRepository.kt
│       │       ├── config/EndpointPolicy.kt
│       │       ├── crypto/AndroidKeystoreCredentialStore.kt
│       │       ├── crypto/DeviceCredentialStore.kt
│       │       ├── crypto/HmacRequestSigner.kt
│       │       ├── local/CollectorDatabase.kt
│       │       ├── local/MessageQueueDao.kt
│       │       ├── local/MessageRecordEntity.kt
│       │       ├── local/QueueRepository.kt
│       │       ├── network/IngestHttpClient.kt
│       │       ├── network/IngestOutcome.kt
│       │       ├── protocol/IngestMessage.kt
│       │       ├── protocol/IngestResponse.kt
│       │       ├── protocol/ProtocolJson.kt
│       │       ├── protocol/StableMessageId.kt
│       │       ├── sms/AndroidSmsIntentParser.kt
│       │       ├── sms/CapturedSms.kt
│       │       ├── sms/SmsCaptureService.kt
│       │       ├── sms/SmsReceivedReceiver.kt
│       │       ├── ui/CollectorApp.kt
│       │       ├── ui/CollectorViewModel.kt
│       │       ├── ui/CollectorViewModelFactory.kt
│       │       ├── ui/model/CollectorUiState.kt
│       │       ├── ui/screen/EnrollmentScreen.kt
│       │       ├── ui/screen/HistoryScreen.kt
│       │       ├── ui/screen/SettingsScreen.kt
│       │       ├── ui/screen/StatusScreen.kt
│       │       ├── ui/theme/Theme.kt
│       │       └── work/
│       │           ├── CollectorWorkerFactory.kt
│       │           ├── UploadScheduler.kt
│       │           └── UploadWorker.kt
│       ├── test/java/io/smsrelay/collector/
│       │   ├── config/EnrollmentRepositoryTest.kt
│       │   ├── crypto/HmacRequestSignerTest.kt
│       │   ├── local/QueueRepositoryTest.kt
│       │   ├── network/IngestHttpClientTest.kt
│       │   ├── protocol/StableMessageIdTest.kt
│       │   ├── sms/AndroidSmsIntentParserTest.kt
│       │   ├── sms/SmsCaptureServiceTest.kt
│       │   ├── ui/CollectorViewModelTest.kt
│       │   └── work/UploadWorkerTest.kt
│       └── androidTest/java/io/smsrelay/collector/
│           ├── crypto/AndroidKeystoreCredentialStoreTest.kt
│           └── ui/CollectorAppTest.kt
├── docs/
│   ├── private-distribution.md
│   └── real-device-validation.md
tests/
├── contracts/android_hmac_v1.json
└── android_ingest_e2e.ps1
```

`MessageRecordEntity` is deliberately both queue and local history. This prevents a second copy of the SMS body and makes acknowledgement redaction transactional. `IngestHttpClient` classifies HTTP only; `UploadWorker` owns state transitions; UI code consumes repository flows and does not call Room, OkHttp, WorkManager, or Keystore directly.

### Task 1: Gradle Project, Protocol JSON, Stable ID, and HMAC

**Files:** Create the Gradle files from the structure above, `protocol/IngestMessage.kt`, `protocol/IngestResponse.kt`, `protocol/ProtocolJson.kt`, `protocol/StableMessageId.kt`, `crypto/HmacRequestSigner.kt`, and their listed unit tests.

- [ ] **Step 1: Write failing protocol and signer tests.** Assert omitted nullable SIM fields, RFC 3339 UTC, stable SHA-256 message IDs, lowercase body digest, and the canonical HMAC/base64url vector stored in `tests/contracts/android_hmac_v1.json`.
- [ ] **Step 2: Run `cd android; .\gradlew.bat testDebugUnitTest --tests "*StableMessageIdTest" --tests "*HmacRequestSignerTest"`; expect compilation failure because production classes do not exist.**
- [ ] **Step 3: Implement the minimum.** Use `Json { encodeDefaults = true; explicitNulls = false }`, `MessageDigest.getInstance("SHA-256")`, `Mac.getInstance("HmacSHA256")`, and `Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP`; sign the exact serialized UTF-8 bytes.
- [ ] **Step 4: Run the two tests and `cd android; .\gradlew.bat lintDebug`; expect all checks to pass.**
- [ ] **Step 5: Commit with `git add android tests/contracts/android_hmac_v1.json && git commit -m "feat(android): define ingest protocol and signing"`.**

### Task 2: Enrollment, HTTPS Policy, DataStore, and Keystore

**Files:** Create `config/DeviceConfig.kt`, `config/DeviceConfigStore.kt`, `config/EndpointPolicy.kt`, `config/EnrollmentRepository.kt`, `crypto/DeviceCredentialStore.kt`, `crypto/AndroidKeystoreCredentialStore.kt`, and `config/EnrollmentRepositoryTest.kt`.

- [ ] **Step 1: Write failing tests.** Accept `https://relay.example`, reject HTTP except `http://10.0.2.2` in debug builds, trim one trailing slash, require nonblank device ID and a 32-byte decoded secret, and assert preferences contain no secret text.
- [ ] **Step 2: Run `cd android; .\gradlew.bat testDebugUnitTest --tests "*EnrollmentRepositoryTest"`; expect unresolved production symbols.**
- [ ] **Step 3: Implement the minimum.** Persist URL/device ID with DataStore; import the decoded secret into a non-exportable `HmacSHA256` Android Keystore key alias `sms-relay-device`; delete the previous alias only after new input validates; return sanitized enrollment errors.
- [ ] **Step 4: Run enrollment tests; expect all pass and no test/log output contains the test secret.**
- [ ] **Step 5: Commit with `git add android/app/src && git commit -m "feat(android): add secure device enrollment"`.**

### Task 3: SMS Broadcast Parsing and Durable Capture

**Files:** Create `sms/CapturedSms.kt`, `sms/AndroidSmsIntentParser.kt`, `sms/SmsCaptureService.kt`, `sms/SmsReceivedReceiver.kt`, `local/MessageRecordEntity.kt`, `local/MessageQueueDao.kt`, `local/CollectorDatabase.kt`, and parser/capture tests; modify `AndroidManifest.xml`.

- [ ] **Step 1: Write failing tests.** Build multipart `SMS_RECEIVED` intents, assert parts are sorted and joined once, sender/time/subscription/slot are mapped, duplicate broadcasts reuse the same stable client ID, and malformed intents are ignored without logging bodies.
- [ ] **Step 2: Run `cd android; .\gradlew.bat testDebugUnitTest --tests "*AndroidSmsIntentParserTest" --tests "*SmsCaptureServiceTest"`; expect failure before parser/database code exists.**
- [ ] **Step 3: Implement the minimum.** Declare `RECEIVE_SMS` and exported=false receiver with the SMS broadcast intent filter; use `Telephony.Sms.Intents.getMessagesFromIntent`; persist before scheduling upload; receiver calls `goAsync()` and always finishes its pending result.
- [ ] **Step 4: Re-run tests and lint; expect multipart, duplicate, invalid, and missing-SIM cases to pass.**
- [ ] **Step 5: Commit with `git add android/app/src android/app/schemas && git commit -m "feat(android): capture incoming sms durably"`.**

### Task 4: Queue Capacity, State Transitions, and Sensitive Redaction

**Files:** Create `local/QueueRepository.kt`, `local/QueueRepositoryTest.kt`; modify DAO/entity definitions.

- [ ] **Step 1: Write failing Room tests.** Insert 501 rows and assert the oldest is removed, newest remains, dropped count increments, pending rows are FIFO, and `markUploaded` atomically nulls sender/body while retaining IDs/timestamps/SIM/server ID.
- [ ] **Step 2: Run `cd android; .\gradlew.bat testDebugUnitTest --tests "*QueueRepositoryTest"`; expect failure.**
- [ ] **Step 3: Implement the minimum in one Room transaction per enqueue/acknowledgement; define `PENDING`, `UPLOADED`, `AUTH_BLOCKED`, and `CONFLICT` states.**
- [ ] **Step 4: Re-run the repository test; expect all capacity and redaction assertions to pass.**
- [ ] **Step 5: Commit with `git add android/app/src && git commit -m "feat(android): manage upload queue safely"`.**

### Task 5: HTTP Client and Ingest Outcome Classification

**Files:** Create `network/IngestHttpClient.kt`, `network/IngestOutcome.kt`, `network/IngestHttpClientTest.kt`.

- [ ] **Step 1: Write MockWebServer tests** for the exact request path/headers/body and outcomes: `201 Accepted`, duplicate `200 Accepted`, `401/403 AuthBlocked`, `409 Conflict`, `429/5xx Retryable`, malformed 2xx `PermanentFailure`, and I/O `Retryable`.
- [ ] **Step 2: Run `cd android; .\gradlew.bat testDebugUnitTest --tests "*IngestHttpClientTest"`; expect failure.**
- [ ] **Step 3: Implement the minimum with OkHttp.** Refuse non-HTTPS release URLs, create a fresh nonce/timestamp per attempt, sign the exact request bytes, cap response bodies, close every response, and never install body/header logging interceptors.
- [ ] **Step 4: Re-run the network tests; expect every status class and header assertion to pass.**
- [ ] **Step 5: Commit with `git add android/app/src && git commit -m "feat(android): upload signed sms messages"`.**

### Task 6: WorkManager Retry and Operator-Visible Blocking

**Files:** Create `work/CollectorWorkerFactory.kt`, `work/UploadScheduler.kt`, `work/UploadWorker.kt`, `work/UploadWorkerTest.kt`; modify `CollectorApplication.kt` and `app/AppContainer.kt`.

- [ ] **Step 1: Write failing worker tests.** Assert FIFO drain, 201/duplicate 200 redaction, auth pause, conflict terminal state, retryable result for 429/5xx/I/O, and no second row upload after a blocking auth result.
- [ ] **Step 2: Run `cd android; .\gradlew.bat testDebugUnitTest --tests "*UploadWorkerTest"`; expect failure.**
- [ ] **Step 3: Implement the minimum.** Register a custom WorkerFactory, enqueue unique work with network constraint, use exponential backoff with 30-45 second initial jitter, and schedule again only while pending work remains and credentials are valid.
- [ ] **Step 4: Re-run worker tests; expect all state transitions and WorkManager results to pass.**
- [ ] **Step 5: Commit with `git add android/app/src && git commit -m "feat(android): retry uploads with workmanager"`.**

### Task 7: Compose Enrollment, Status, History, Settings, and Permissions

**Files:** Create all listed `ui/` files, `MainActivity.kt`, `CollectorViewModel.kt`, `CollectorViewModelFactory.kt`, `ui/model/CollectorUiState.kt`, UI/ViewModel tests, and theme resources.

- [ ] **Step 1: Write failing ViewModel and Compose tests.** Cover first-run enrollment, permission denied, active status, pending/failed counts, masked history, SIM 1/2 rendering, credential replacement confirmation, and no SMS body after successful acknowledgement.
- [ ] **Step 2: Run `cd android; .\gradlew.bat testDebugUnitTest --tests "*CollectorViewModelTest"`; expect failure.**
- [ ] **Step 3: Implement the minimum quiet operational UI.** Use tabs for Status/History/Settings, icon buttons with content descriptions, runtime `RECEIVE_SMS` permission flow, compact error banners, and no feature-explanation or marketing copy.
- [ ] **Step 4: Run unit tests, `connectedDebugAndroidTest` when an emulator/device exists, and lint; expect no text overflow or accessibility assertion failures.**
- [ ] **Step 5: Commit with `git add android/app/src && git commit -m "feat(android): add collector operations ui"`.**

### Task 8: Private Distribution, CI Build, Contract Smoke, and Real Device Gate

**Files:** Create `android/README.md`, `android/docs/private-distribution.md`, `android/docs/real-device-validation.md`, `.github/workflows/android.yml`, `tests/android_ingest_e2e.ps1`; modify `.gitignore` for signing/local files only.

- [ ] **Step 1: Write the PowerShell contract smoke script** to load a real device ID/secret from environment variables, generate a sanitized synthetic message, compute the locked HMAC vector, post it twice, and assert `201` then duplicate `200` without printing body or secrets.
- [ ] **Step 2: Run the script without environment variables; expect a fail-closed message naming missing variable names only.**
- [ ] **Step 3: Document private APK signing, sideloading, SMS permission/Google Play restrictions, battery optimization notes, dual-SIM behavior, credential rotation, and a real-device matrix for lock screen/offline/reboot/process kill/recovery. CI uses JDK 17 and runs unit tests, lint, and `assembleDebug`; signing secrets are never committed.**
- [ ] **Step 4: On a machine with JDK 17 and Android SDK, run `cd android; .\gradlew.bat clean testDebugUnitTest lintDebug assembleDebug`; then execute the real-device checklist and contract smoke against HTTPS. Do not mark Android complete without these results.**
- [ ] **Step 5: Commit with `git add android tests/android_ingest_e2e.ps1 .github/workflows/android.yml .gitignore && git commit -m "docs(android): add distribution and validation workflow"`.**

## Final Android Verification Gate

```powershell
cd android
.\gradlew.bat clean testDebugUnitTest lintDebug assembleDebug
```

Expected: all unit tests and lint pass, and `android/app/build/outputs/apk/debug/app-debug.apk` exists. Run connected tests and the real-device checklist when Android tooling and a device are available; otherwise report the build/real-device verification as not run.
