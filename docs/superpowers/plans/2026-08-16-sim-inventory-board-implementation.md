# SIM Inventory And Board Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track SIM slots and phone-number mappings and provide a vendor-neutral bridge for SIM-board platforms until a concrete vendor API is supplied.

**Architecture:** Add a `device_sim_profiles` table keyed by device and slot. Provide authenticated admin CRUD for manual mappings. Add a board bridge package that normalizes vendor payloads into `sim_board` ingest messages and signs them with the same HMAC protocol. The bridge supports webhook input and a polling adapter interface; vendor-specific code is isolated under `clients/sim-board/drivers/`.

**Tech Stack:** FastAPI, SQLAlchemy, SQLite/PostgreSQL-compatible schema, Python adapter package, JSON/HMAC.

---

### Task 1: Lock SIM mapping and board normalization behavior

**Files:**
- Modify: `backend/tests/test_relay.py`
- Create: `clients/sim-board/tests/test_normalize.py`

- [x] **Step 1:** Add failing tests for creating/updating/deleting a slot mapping, masking phone numbers in list responses, normalizing a board payload, and rejecting a board payload with mismatched device/source.
- [x] **Step 2:** Run the focused tests and confirm failure before models/routes/adapter code exist.

### Task 2: Add SIM profile persistence and admin API

**Files:**
- Modify: `backend/app/db/models.py`
- Modify: `backend/app/services/store.py`
- Modify: `backend/app/api/relay.py`
- Modify: `backend/tests/test_relay.py`

- [x] **Step 1:** Add `DeviceSimProfileModel` with device/slot uniqueness and optional subscription, encrypted phone, carrier, and source fields.
- [x] **Step 2:** Add store methods for list/upsert/delete profiles and redact all but the final four phone digits in admin responses.
- [x] **Step 3:** Add list and single-slot `GET`, `PUT`, and `DELETE` admin routes for device SIM profiles.
- [x] **Step 4:** Include `phone_number` only in create/update input and never in client logs, database plaintext columns, API output, or SMS bodies.
- [x] **Step 5:** Add the `20260817_0002` Alembic upgrade/downgrade revision with encrypted phone columns.
- [x] **Step 6:** Add the management-console SIM mapping list, create/edit, explicit phone clearing, delete, and stale-response protection.

### Task 3: Add Android phone/SIM metadata contract

**Files:**
- Modify: `android/app/src/main/java/io/smsrelay/collector/SmsReceivedReceiver.kt`
- Modify: `android/app/src/main/java/io/smsrelay/collector/SmsRelayPayload.kt`
- Modify: `android/README.md`
- Add tests under: `android/app/src/test/kotlin/io/smsrelay/collector/`

- [x] **Step 1:** Add a `SubscriptionSnapshot` provider that reads slot, subscription ID, carrier, and attempts `TelephonyManager.line1Number` only when permission and platform support allow it.
- [x] **Step 2:** Add optional metadata fields to the payload; omit unavailable values rather than blocking SMS capture.
- [x] **Step 3:** Add tests for available and unavailable phone-number cases and document manual mapping fallback.

### Task 4: Implement the generic SIM-board bridge

**Files:**
- Create: `clients/sim-board/README.md`
- Create: `clients/sim-board/config.example.json`
- Create: `clients/sim-board/normalize.py`
- Create: `clients/sim-board/webhook.py`
- Create: `clients/sim-board/polling.py`
- Create: `clients/sim-board/drivers/README.md`
- Create: `clients/sim-board/tests/test_normalize.py`

- [x] **Step 1:** Normalize common vendor fields (`slot`, `phone_number`, `sender`, `body`, `received_at`, `message_id`) into the existing schema with source `sim_board`.
- [x] **Step 2:** Sign and post normalized messages using the device HMAC credential and stable idempotency key.
- [x] **Step 3:** Provide webhook and polling entry points with explicit driver boundaries; do not claim a vendor-specific driver without its API.
- [x] **Step 4:** Document the exact vendor information required to implement a driver: brand/model, transport, endpoint/auth, slot/number fields, acknowledgement semantics.

### Task 5: Verify SIM and board behavior

- [x] **Step 1:** Run backend tests and adapter tests.
- [x] **Step 2:** Run Android unit tests if the local Android toolchain is available; otherwise record the missing JDK/SDK.
- [x] **Step 3:** Record the vendor-specific board integration as pending until the user supplies the model/API.

---

## Execution evidence

### SIM profile persistence, API, migration, and admin UI

The implementation was performed in the existing `feature/phase-1` worktree without
committing, staging, or replacing the accumulated client work. The original plan was
corrected during execution to include the required Alembic migration and admin UI that
were present in the approved design but missing from the task list.

Initial RED evidence:

- Backend focused tests failed at collection because `DeviceSimProfileModel` did not
  exist yet.
- Migration tests reported `1 failed, 1 passed` because the Alembic head did not create
  `device_sim_profiles`.
- Frontend tests reported `4 failed, 11 passed` because the device table had no SIM
  mapping entry point.
- An added invalid-source case for list/dict values reproduced an unhandled
  `TypeError: unhashable type`; the validation was then hardened to return 422.

Current GREEN evidence:

- `backend\.venv\Scripts\python.exe -m pytest backend\tests -q`:
  `60 passed, 1 warning`.
- `backend\.venv\Scripts\python.exe -m ruff check backend\app backend\tests backend\migrations`:
  `All checks passed!`.
- `backend\.venv\Scripts\python.exe -m mypy --no-incremental backend\app`:
  `Success: no issues found in 16 source files`.
- `npm test`: `16 passed`; `npm run typecheck` and `npm run build` passed.

Spec-review RED evidence for the short-number boundary:

- Added tests for `"1"`, `"1234"`, empty, and whitespace-only phone values before
  changing production code.
- Focused command:
  `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_relay.py -k "fully_masks_phone_numbers_without_a_safe_suffix or rejects_blank_phone_numbers" -q`.
- Result before the fix: `4 failed, 37 deselected, 1 warning`. Short values were
  returned verbatim and blank values were accepted.

Spec-review GREEN evidence:

- The same focused command after the minimal fix returned
  `4 passed, 37 deselected, 1 warning`.
- Phone values of four or fewer characters are now fully masked, and empty or
  whitespace-only strings are rejected with `invalid_sim_profile`.

The warning in these backend runs is the existing Starlette/httpx TestClient
deprecation warning and is not a SIM behavior failure.

Final quality-review evidence:

- AES-GCM storage, phone normalization, PostgreSQL INTEGER bounds, two-store
  serialization, and stale-modal response tests are included in the backend/web totals
  above.
- Added `+1`, `+1234`, and `+12345` mask cases. Before the final fix, the focused run
  reported `1 failed, 4 passed, 47 deselected`; `+1234` incorrectly returned `*1234`.
- The final focused command covering the short-number and leading-plus tables returned
  `2 passed, 47 deselected, 1 warning`.
- Masking now uses digit count: normalized values with four or fewer digits are fully
  masked even with a leading `+`; longer values reveal only their final four digits.

### Android subscription snapshot and receiver hardening

The Android implementation added permission-gated subscription and line-number lookup,
payload omission rules, encrypted queued phone metadata, and a Room v1-to-v2 migration.
The required `RECEIVE_SMS` request remains independent from optional phone permissions.

Review RED evidence:

- Added Kotlin cases for a stale subscription ID with a valid slot, conflicting ID/slot
  hints, and a subscription ID proven missing by protected lookup.
- Extended `android/tests/subscription_snapshot_contract.ps1` to require ID-to-slot
  fallback and a sanitized outer `goAsync` worker exception boundary.
- Before production changes, the harness exited `1` with
  `Missing contract 'subscription ID lookup'`.

Review GREEN evidence:

- `pwsh -NoProfile -File android/tests/subscription_snapshot_contract.ps1` exits `0`
  with `PASS: Android subscription metadata source contract`.
- Active subscription records now override conflicting broadcast hints. A proven stale
  ID falls back to slot lookup and is not emitted when no active record exists.
- The receiver catches only non-fatal `Exception` values at the worker boundary,
  attempts a constant sanitized status, protects `pendingResult.finish()` in `finally`,
  and does not catch `Throwable` or `Error`.
- Java, javac, Gradle, kotlinc, `ANDROID_HOME`, and `ANDROID_SDK_ROOT` are unavailable on
  this workstation. Kotlin tests, lint, and APK builds were not executed and are not
  reported as passing.

External Android acceptance remains the CI compile/test/lint/build job plus real-device
single/dual-SIM, permission denial, stale/conflicting broadcast hint, Room migration,
and injected receiver failure validation.

Second Android quality-review evidence:

- Split the test fake's subscription-ID, slot, and single-subscription failures so mixed
  lookup outcomes can be reproduced independently.
- Added a regression case for `ID Failed + slot Missing`: the broadcast subscription ID
  remains as an unverified best-effort hint, while phone-number lookup is skipped.
- Before the production fix, the source-contract harness exited `1` with
  `Missing contract 'failed subscription hint preservation'`.
- After the minimal lookup-state fix, the harness exits `0` with
  `PASS: Android subscription metadata source contract`.
- A subscription ID is now cleared only when its lookup and every supplied fallback are
  explicitly `Missing`; any unresolved `Failed` result keeps the overall decision
  `Failed`. Kotlin/Gradle tests remain unexecuted because the local toolchain is absent.

The final Android quality re-review reported Critical `0`, Important `0`, Minor `0`,
and `Ready`. Android SDK/JDK/Gradle and real-device validation remain external gates.

### Generic SIM-board bridge

Initial RED evidence:

- `backend\.venv\Scripts\python.exe -m pytest clients\sim-board\tests\test_normalize.py -q`
  failed during collection because `normalize.py` did not exist.
- Review-driven tests then failed before their production fixes: the polling loop did
  not expose `run_forever`, public example placeholders were accepted, Unicode phone
  digits were accepted, and a general `import_module()` call executed before driver
  origin validation.
- The pre-fix driver-isolation run reported `2 failed`: the external importer was
  called and a valid local temporary driver could not be loaded through the guarded
  path.
- The final quality-review RED run reported `6 failed, 37 passed`: invalid UTF-8
  disconnected the webhook, module/factory and `poll`/`acknowledge` exceptions escaped
  their plugin boundary, and state-directory creation escaped cursor error handling.

Current GREEN evidence:

- `backend\.venv\Scripts\python.exe -m pytest clients\sim-board\tests -q`:
  `43 passed`.
- `backend\.venv\Scripts\python.exe -m ruff check clients\sim-board`:
  `All checks passed!`.
- All three production Python entrypoint files compile in memory without writing build
  artifacts, and `git diff --check -- clients/sim-board` passes.
- Tests cover field normalization, timezone conversion, stable IDs, ASCII phone
  validation, exact HMAC bytes and headers, unsafe configuration, webhook bearer auth,
  request size, invalid UTF-8 and JSON validation, relay status mapping, polling network
  failures, sanitized module/factory/driver exceptions, accept-before-ack, cursor
  preservation, state-directory and atomic replacement failures, and bounded
  retry/permanent-error behavior.
- Driver loading is restricted to a validated local
  `clients/sim-board/drivers/<name>.py` path before module code executes; public example
  driver names and missing modules fail as sanitized configuration errors.
- The final quality re-review reported Critical `0`, Important `0`, Minor `0`, and
  `Ready`.

Fresh full repository verification after the final exception-boundary fixes:

- Backend: `60 passed, 1` existing Starlette/httpx deprecation warning; Ruff and mypy
  pass.
- Web: `16 passed`; TypeScript typecheck and production build pass.
- Root/Ve verifier: both signature vectors, 28 PowerShell contract cases, full Root
  POSIX behavior, Ve source contract, and all adapter entrypoints pass.
- Android source contract and `git diff --check` pass.

The bridge remains vendor-neutral. No brand, model, firmware, endpoint, authentication,
field map, cursor, or acknowledgement behavior has been supplied for a real SIM board,
so concrete vendor compatibility remains pending and is not reported as complete.
