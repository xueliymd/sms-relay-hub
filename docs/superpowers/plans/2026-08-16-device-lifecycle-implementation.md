# Device Lifecycle And Admin UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add distinct revoke, restore-with-new-credential, and permanent purge operations, and expose all supported client types in the management UI.

**Architecture:** Keep revocation as an inactive row for audit. Add explicit restore and purge endpoints. Restore rotates the credential in the same transaction; purge cascades device-owned data and keeps only a non-sensitive audit event. Extend the existing `DEVICE_CONTRACTS` map so the server, UI, and ingest source validation use one contract table.

**Tech Stack:** FastAPI, SQLAlchemy, SQLite/PostgreSQL-compatible models, React, TypeScript, Vitest, pytest.

---

### Task 1: Lock the lifecycle contract with backend tests

**Files:**
- Modify: `backend/tests/test_relay.py`
- Test: existing pytest suite

- [ ] **Step 1: Add failing tests** for restore returning a new credential, invalidating the old credential, rejecting restore on active devices, allowing purge only on revoked devices, and removing the device and its dependent messages/OTPs after purge.
- [ ] **Step 2: Run** `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_relay.py -q` and confirm the new tests fail because the endpoints and store methods do not exist.

### Task 2: Implement store and API lifecycle operations

**Files:**
- Modify: `backend/app/services/store.py`
- Modify: `backend/app/api/relay.py`
- Modify: `backend/app/db/models.py`

- [ ] **Step 1:** Add `restore_device()` that generates a fresh Bearer/HMAC secret, sets `active=True`, updates the credential fields, and audits `device.restored`.
- [ ] **Step 2:** Add `purge_device()` that requires `active=False`, deletes device-owned nonces, OTPs/messages, and the device row in one transaction, then audits `device.purged` without sensitive fields.
- [ ] **Step 3:** Add `POST /api/v1/admin/devices/{id}/restore` and `DELETE /api/v1/admin/devices/{id}/purge`; keep existing `DELETE /api/v1/admin/devices/{id}` as revoke.
- [ ] **Step 4:** Add `sim_board`, `root_module`, and `ve_tweak` to `DEVICE_CONTRACTS`, mapping each to HMAC and its matching source.
- [ ] **Step 5:** Re-run the focused tests, then the complete backend suite.

### Task 3: Add lifecycle controls and client choices to the UI

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/App.test.tsx`
- Modify: `web/src/App.css`

- [ ] **Step 1:** Add failing tests for five client options, restore action on revoked devices, purge confirmation requiring the device name, and separate revoke/purge controls.
- [ ] **Step 2:** Run `npm --prefix web test -- --run src/App.test.tsx` and confirm failure.
- [ ] **Step 3:** Add `Undo2`/`Trash2` controls, restore and purge request functions, and a destructive confirmation dialog; keep rotation/revoke disabled for inactive rows.
- [ ] **Step 4:** Render client labels for Android App, Android Root, iPhone Shortcut, iOS Ve, and SIM 卡板. Use the correct auth kind for each create request.
- [ ] **Step 5:** Run the focused UI tests, typecheck, and production build.

### Task 4: Verify lifecycle behavior end to end

- [ ] **Step 1:** Run backend pytest, Ruff, and mypy.
- [ ] **Step 2:** Run frontend Vitest, TypeScript, and build.
- [ ] **Step 3:** Use the local preview API to create, revoke, restore, and purge a test device and record response codes without recording credentials.
