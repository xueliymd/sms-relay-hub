# SMS Relay Hub Server, Admin UI, and Compose Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Phase 1 FastAPI/PostgreSQL service, minimal React administration UI, and Docker Compose deployment for secure SMS ingest, OTP parsing and atomic claim, auditing, notification delivery, and live updates.

**Architecture:** One FastAPI process owns the versioned APIs, PostgreSQL transactions, scheduled retention/webhook workers, SSE stream, and compiled React assets. PostgreSQL is the sole durable source; sensitive fields use application-level AES-256-GCM, device/API credentials are one-time values, and concurrent OTP claim uses `FOR UPDATE SKIP LOCKED`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic, PostgreSQL 16, Pydantic Settings, cryptography AESGCM, Argon2id, pytest, React, TypeScript, Vite, Vitest, Testing Library, TanStack Query, Lucide React, Docker Compose, Caddy.

---

## Fixed Contracts

- Repository areas: `backend/`, `web/`, `deploy/`, `tests/`, `debug/`; this plan does not cover Android or iOS client implementation.
- `POST /api/v1/ingest/messages`: first insert returns `201`; same device/idempotency key and identical body returns `200` with `{"accepted":true,"duplicate":true,"message_id":"uuid"}`; same key with a different body returns `409` and `{"error":{"code":"idempotency_conflict","message":"Idempotency key was already used with a different request body"}}`.
- Every error uses `{"error":{"code":"string","message":"string"}}`.
- HMAC timestamp is Unix seconds; signature is unpadded base64url of `HMAC-SHA256(secret, timestamp + "\n" + nonce + "\n" + sha256(raw_body_hex))`; clock skew is at most 300 seconds and nonce reuse is rejected.
- Device administration: `POST /api/v1/admin/devices`, `POST /api/v1/admin/devices/{device_id}/rotate-credential`, `DELETE /api/v1/admin/devices/{device_id}`. Android uses HMAC; iOS Shortcut uses Bearer.
- OTP expiration is `expires_at <= now`; retention redacts ciphertext and metadata while preserving IDs, timestamps, sender summary, status, counts, and audit references.

## File Map

- `backend/pyproject.toml`, `backend/alembic.ini`, `backend/migrations/`: dependencies and schema migrations.
- `backend/app/core/`: settings, error envelope, cryptography, password/session helpers, redacted logging.
- `backend/app/db/`: async engine/session and focused SQLAlchemy models.
- `backend/app/services/`: audit, devices, ingest, OTP parser/claim, retention, webhook outbox, SSE events.
- `backend/app/api/`: admin, ingest, claim, health, and SSE routes; `backend/app/main.py` composes them and serves `backend/app/static/`.
- `backend/tests/unit/`, `backend/tests/integration/`: isolated logic and real-PostgreSQL transaction tests.
- `web/src/`: typed API client, auth shell, dashboard/inbox/OTP/devices/settings views and component tests.
- `deploy/compose.yml`, `deploy/Dockerfile`, `deploy/Caddyfile`, `deploy/entrypoint.sh`, `deploy/.env.example`: reproducible deployment.
- `tests/test_phase1_e2e.py`: live Compose protocol acceptance; `debug/` is runtime-only and gitignored.

### Task 1: Project Skeleton and Health Contract
**Files:** Create `backend/pyproject.toml`, `backend/app/__init__.py`, `backend/app/main.py`, `backend/app/api/health.py`, `backend/tests/test_health.py`, `backend/tests/conftest.py`, `web/package.json`, `web/tsconfig.json`, `web/vite.config.ts`, `web/src/main.tsx`, `web/src/App.tsx`, `web/src/test/setup.ts`, `web/src/App.test.tsx`, `.gitignore`.

- [ ] **Step 1: Write the failing tests**

```python
def test_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

```tsx
it("renders the authenticated application root", () => {
  render(<App />)
  expect(screen.getByRole("main")).toBeInTheDocument()
})
```

- [ ] **Step 2: Run and verify failure**
  Run: `cd backend; py -3.12 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -e ".[dev]"; .\.venv\Scripts\python.exe -m pytest tests/test_health.py -q` and `cd web; npm install; npm test -- --run src/App.test.tsx`.
  Expected: backend fails because `app.main` is missing; web fails because `App` has no `<main>`.
- [ ] **Step 3: Implement the minimum**: create `FastAPI(title="SMS Relay Hub", version="1.0.0")`, include a health router returning `{"status":"ok"}`, add pytest/TestClient fixtures, and render an accessible `<main>` from React. Configure Ruff, mypy, pytest, Vitest/jsdom, Testing Library, React Query, Router, and Lucide dependencies in the declared manifests.
- [ ] **Step 4: Run and verify pass**
  Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/test_health.py -q; .\.venv\Scripts\python.exe -m ruff check app tests` and `cd web; npm test -- --run; npm run typecheck`.
  Expected: health test passes, Ruff reports no errors, Vitest passes, and TypeScript exits `0`.
- [ ] **Step 5: Commit**: `git add .gitignore backend web && git commit -m "chore: scaffold server and admin ui"`.

### Task 2: Configuration, PostgreSQL Models, and Migration
**Files:** Create `backend/app/core/config.py`, `backend/app/db/base.py`, `backend/app/db/session.py`, `backend/app/db/models/{admin,device,message,otp,audit,notification,event}.py`, `backend/app/db/models/__init__.py`, `backend/alembic.ini`, `backend/migrations/env.py`, `backend/migrations/versions/20260815_0001_initial.py`, `backend/tests/integration/test_migrations.py`.

- [ ] **Step 1: Write the failing test**

```python
EXPECTED = {"admins", "admin_sessions", "devices", "device_nonces", "api_clients", "messages", "otp_rules", "otps", "system_settings", "audit_events", "notification_rules", "webhook_deliveries", "event_stream"}
async def test_upgrade_head_creates_schema(pg_connection):
    names = set(await pg_connection.run_sync(lambda c: inspect(c).get_table_names()))
    assert EXPECTED <= names
```

- [ ] **Step 2: Run and verify failure**
  Run: `cd backend; $env:TEST_DATABASE_URL='postgresql+asyncpg://sms_relay:sms_relay@localhost:55432/sms_relay_test'; .\.venv\Scripts\python.exe -m pytest tests/integration/test_migrations.py -q`.
  Expected: fails because Alembic configuration and tables do not exist.
- [ ] **Step 3: Implement the minimum**: define validated settings for database URL, 32-byte base64 master key, admin bootstrap credentials, cookie security, 7-day retention, 600-second OTP TTL, and 300-second HMAC skew. Add UUID primary keys, timezone-aware timestamps, unique `(device_id, client_message_id)` and `(device_id, nonce)` constraints, encrypted-field nonce/ciphertext/key-version columns, OTP status enum, JSONB audit details, webhook attempt fields, and monotonic event IDs. Make migration upgrade/downgrade complete and import all metadata in `migrations/env.py`.
- [ ] **Step 4: Run and verify pass**
  Run: `cd backend; .\.venv\Scripts\python.exe -m alembic upgrade head; .\.venv\Scripts\python.exe -m pytest tests/integration/test_migrations.py -q`.
  Expected: Alembic reaches `20260815_0001`, all named tables and constraints exist, and test passes.
- [ ] **Step 5: Commit**: `git add backend/app/core/config.py backend/app/db backend/alembic.ini backend/migrations backend/tests/integration/test_migrations.py && git commit -m "feat: add postgres schema and settings"`.

### Task 3: AES-GCM Field Encryption
**Files:** Create `backend/app/core/crypto.py`, `backend/tests/unit/test_crypto.py`; modify encrypted model type annotations from Task 2 only if the test exposes a mismatch.

- [ ] **Step 1: Write the failing test**

```python
def test_ciphertext_requires_matching_aad(field_crypto):
    value = field_crypto.encrypt_text("482731", "otp:7:code:v1")
    assert field_crypto.decrypt_text(value, "otp:7:code:v1") == "482731"
    with pytest.raises(InvalidTag):
        field_crypto.decrypt_text(value, "otp:8:code:v1")
```

- [ ] **Step 2: Run and verify failure**
  Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/unit/test_crypto.py -q`.
  Expected: fails with `ModuleNotFoundError: app.core.crypto`.
- [ ] **Step 3: Implement the minimum**: add immutable `EncryptedValue(nonce: bytes, ciphertext: bytes, key_version: int)`, require a decoded 32-byte AES key, generate a fresh 12-byte nonce, encode AAD as UTF-8, and expose `encrypt_text`/`decrypt_text`. Use AAD `message:{uuid}:body:v1`, `device:{uuid}:secret:v1`, `otp:{uuid}:code:v1`, and `notification:{uuid}:endpoint:v1`.
- [ ] **Step 4: Run and verify pass**
  Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/unit/test_crypto.py -q`.
  Expected: round-trip, random nonce, tamper, and wrong-AAD tests all pass.
- [ ] **Step 5: Commit**: `git add backend/app/core/crypto.py backend/tests/unit/test_crypto.py backend/app/db/models && git commit -m "feat: encrypt sensitive fields with aes gcm"`.

### Task 4: Administrator Bootstrap, Session, CSRF, and Error Envelope
**Files:** Create `backend/app/core/security.py`, `backend/app/core/errors.py`, `backend/app/services/admin_auth.py`, `backend/app/services/audit.py`, `backend/app/api/admin_auth.py`, `backend/tests/integration/test_admin_auth.py`; modify `backend/app/main.py`.

- [ ] **Step 1: Write the failing test**

```python
async def test_login_issues_secure_session_and_csrf(client, bootstrapped_admin):
    response = await client.post("/api/v1/admin/auth/login", json={"username":"owner","password":"correct horse battery staple"})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=lax" in response.headers["set-cookie"]
    csrf = response.json()["csrf_token"]
    denied = await client.post("/api/v1/admin/auth/logout")
    assert denied.json()["error"]["code"] == "csrf_required"
    assert (await client.post("/api/v1/admin/auth/logout", headers={"X-CSRF-Token": csrf})).status_code == 204
```

- [ ] **Step 2: Run and verify failure**
  Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/integration/test_admin_auth.py -q`.
  Expected: fails with `404 Not Found` for the login route.
- [ ] **Step 3: Implement the minimum**: bootstrap exactly one admin from settings when absent; hash passwords with Argon2id; store only SHA-256 hashes of 32-byte opaque session and CSRF tokens; set `Secure`, `HttpOnly`, `SameSite=Lax`, path `/`, and 12-hour expiry; require `X-CSRF-Token` on cookie-authenticated mutations; implement login/session/logout/password-change; emit audit events without password, token, body, OTP, or secret values; install the fixed error-envelope handler.
- [ ] **Step 4: Run and verify pass**
  Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/integration/test_admin_auth.py -q`.
  Expected: success, invalid credential, expiry, CSRF, logout, password change, and audit assertions pass.
- [ ] **Step 5: Commit**: `git add backend/app/core backend/app/services/admin_auth.py backend/app/services/audit.py backend/app/api/admin_auth.py backend/app/main.py backend/tests/integration/test_admin_auth.py && git commit -m "feat: add secure administrator sessions"`.

### Task 5: Device Credentials and Administration

**Files:** Create `backend/app/schemas/device.py`, `backend/app/services/devices.py`, `backend/app/api/admin_devices.py`, `backend/tests/integration/test_admin_devices.py`; modify `backend/app/main.py`.

- [ ] **Step 1: Write the failing test**

```python
async def test_hmac_secret_is_shown_once(admin_client):
    created = await admin_client.post("/api/v1/admin/devices", json={"name":"Pixel 9","client_type":"android_app","auth_kind":"hmac"})
    assert created.status_code == 201 and created.json()["credential"]["type"] == "hmac"
    device_id = created.json()["device"]["id"]
    listing = await admin_client.get("/api/v1/admin/devices")
    assert "secret" not in listing.text
    rotated = await admin_client.post(f"/api/v1/admin/devices/{device_id}/rotate-credential")
    assert rotated.json()["credential"]["secret"] != created.json()["credential"]["secret"]
```

- [ ] **Step 2: Run and verify failure**
  Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/integration/test_admin_devices.py -q`.
  Expected: fails with `404 Not Found` for device creation.
- [ ] **Step 3: Implement the minimum**: create/list/update devices; return `srh_dev_` plus 32 random bytes for Bearer and a 32-byte base64url secret for HMAC exactly once; store Bearer SHA-256 only and HMAC secret encrypted; bind `ios_shortcut` to Bearer and `android_app` to HMAC; rotate atomically; revoke with `DELETE` and retain audit history; expose active state, client type/version, and last report time.
- [ ] **Step 4: Run and verify pass**
  Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/integration/test_admin_devices.py -q`.
  Expected: one-time display, type enforcement, rotation invalidation, revocation, and audit tests pass.
- [ ] **Step 5: Commit**: `git add backend/app/schemas/device.py backend/app/services/devices.py backend/app/api/admin_devices.py backend/app/main.py backend/tests/integration/test_admin_devices.py && git commit -m "feat: manage device credentials"`.

### Task 6: Authenticated and Idempotent Message Ingest

**Files:** Create `backend/app/schemas/ingest.py`, `backend/app/services/device_auth.py`, `backend/app/services/ingest.py`, `backend/app/api/ingest.py`, `backend/tests/integration/test_ingest.py`, `backend/tests/unit/test_device_auth.py`; modify `backend/app/main.py`.

- [ ] **Step 1: Write the failing test**

```python
async def test_idempotency_contract(device_client, payload):
    first = await device_client.post("/api/v1/ingest/messages", json=payload, headers={"X-Idempotency-Key":"m-17"})
    duplicate = await device_client.post("/api/v1/ingest/messages", json=payload, headers={"X-Idempotency-Key":"m-17"})
    changed = await device_client.post("/api/v1/ingest/messages", json={**payload,"body":"验证码 927415"}, headers={"X-Idempotency-Key":"m-17"})
    assert (first.status_code, duplicate.status_code, changed.status_code) == (201, 200, 409)
    assert duplicate.json()["duplicate"] is True
    assert changed.json()["error"]["code"] == "idempotency_conflict"
```

- [ ] **Step 2: Run and verify failure**
  Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/unit/test_device_auth.py tests/integration/test_ingest.py -q`.
  Expected: fails because ingest/auth services do not exist.
- [ ] **Step 3: Implement the minimum**: validate schema version `1`, idempotency header equals `client_message_id`, body at most 4096 characters, serialized metadata at most 8192 bytes, source matches device, and timestamps are timezone-aware. Authenticate Bearer hash or raw-body HMAC; reject skew/replay; hash canonical request bytes for idempotency comparison; encrypt body before flush; use the database unique constraint to resolve races; update last-seen; never log request body or credentials.
- [ ] **Step 4: Run and verify pass**
  Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/unit/test_device_auth.py tests/integration/test_ingest.py -q`.
  Expected: Bearer/HMAC valid, invalid, expired, replay, size, source, first/duplicate/conflict, and concurrent duplicate tests pass.
- [ ] **Step 5: Commit**: `git add backend/app/schemas/ingest.py backend/app/services/device_auth.py backend/app/services/ingest.py backend/app/api/ingest.py backend/app/main.py backend/tests && git commit -m "feat: ingest messages idempotently"`.

### Task 7: OTP Parser, API Clients, and Atomic Claim

**Files:** Create `backend/app/services/otp_parser.py`, `backend/app/services/otp_claim.py`, `backend/app/services/api_clients.py`, `backend/app/schemas/otp.py`, `backend/app/api/otp_claim.py`, `backend/app/api/admin_api_clients.py`, `backend/tests/unit/test_otp_parser.py`, `backend/tests/integration/test_otp_claim.py`; modify `backend/app/services/ingest.py`, `backend/app/main.py`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize(("body","code"), [("您的验证码为 482731，10分钟有效","482731"),("OTP code: 8194","8194"),("订单 202608150001 金额 1280 元",None)])
def test_default_parser(parser, body, code):
    result = parser.parse(sender="service", body=body, received_at=NOW)
    assert (result.code if result else None) == code
```

```python
async def test_two_claimers_never_receive_same_otp(claim_twice_concurrently):
    results = await claim_twice_concurrently()
    assert sorted(r.status_code for r in results) == [200, 404]
```

- [ ] **Step 2: Run and verify failure**
  Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/unit/test_otp_parser.py tests/integration/test_otp_claim.py -q`.
  Expected: fails because parser and claim route are absent.
- [ ] **Step 3: Implement the minimum**: score keyword-backed 4-8 digit candidates; exclude phone-length numbers, years, currency-adjacent values, and order-number contexts; apply prioritized sender regex rules and per-rule TTL; encrypt accepted codes. Create one-time `srh_api_` client tokens with optional device/sender scopes. Claim only `available` rows with `expires_at > now`, enforce scopes and filters, use `SELECT FOR UPDATE SKIP LOCKED`, decrypt body in-transaction for optional keyword matching, mark exactly one OTP claimed, and return `otp_not_available` as `404` thereafter.
- [ ] **Step 4: Run and verify pass**
  Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/unit/test_otp_parser.py tests/integration/test_otp_claim.py -q`.
  Expected: parser corpus, custom rules, exact 10-minute boundary, scope restrictions, one-time token display, and 20-way concurrency tests pass.
- [ ] **Step 5: Commit**: `git add backend/app/services backend/app/schemas/otp.py backend/app/api backend/app/main.py backend/tests && git commit -m "feat: parse and atomically claim otp codes"`.

### Task 8: Retention, Admin Read APIs, Audit, Webhook Outbox, and SSE

**Files:** Create `backend/app/services/{retention,webhooks,events,overview}.py`, `backend/app/api/{admin_messages,admin_otps,admin_settings,admin_notifications,admin_audit,events}.py`, `backend/app/workers.py`, `backend/tests/integration/test_operations.py`; modify `backend/app/main.py`.

- [ ] **Step 1: Write the failing test**

```python
async def test_cleanup_redacts_and_preserves_evidence(operations):
    result = await operations.run_retention(now=NOW)
    message = await operations.load_message()
    assert result.redacted_messages == 1
    assert message.body_ciphertext is None and message.metadata == {}
    assert message.sender_summary and await operations.audit_count() > 0
```

- [ ] **Step 2: Run and verify failure**
  Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/integration/test_operations.py -q`.
  Expected: fails because operations services and admin routes are absent.
- [ ] **Step 3: Implement the minimum**: add masked list endpoints plus audited reveal/copy/void/delete actions; configurable retention and OTP TTL; idempotent redaction and expiry; allowlisted audit details; webhook rules with encrypted URL/signing secret; transactional delivery rows, immediate independent worker, attempts at 0/30/120/600/3600 seconds, five-attempt terminal failure, and no sensitive retry payload. Persist SSE event rows containing type/entity ID only; stream by `Last-Event-ID` with 15-second heartbeat; overview reads database counts. Guard periodic cleanup with a PostgreSQL advisory lock.
- [ ] **Step 4: Run and verify pass**
  Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests/integration/test_operations.py -q`.
  Expected: masking/audit, manual and scheduled redaction, retry isolation, signed webhook, resume-safe SSE, overview, and secret-free logging tests pass.
- [ ] **Step 5: Commit**: `git add backend/app/services backend/app/api backend/app/workers.py backend/app/main.py backend/tests/integration/test_operations.py && git commit -m "feat: add operations audit notifications and events"`.

### Task 9: Minimal Responsive React Administration UI

**Files:** Create `web/src/api/{client,types}.ts`, `web/src/auth/AuthProvider.tsx`, `web/src/layout/AppShell.tsx`, `web/src/pages/{Login,Overview,Messages,Otps,Devices,Settings}.tsx`, `web/src/components/{MaskedValue,CredentialDialog,StatusBadge}.tsx`, `web/src/styles.css`, `web/src/pages/AdminFlows.test.tsx`; modify `web/src/App.tsx`, `web/src/main.tsx`.

- [ ] **Step 1: Write the failing test**

```tsx
it("keeps sensitive values masked until an audited action", async () => {
  renderAppAt("/messages")
  expect(await screen.findByText("••••••")).toBeInTheDocument()
  await userEvent.click(screen.getByRole("button", {name: "Reveal message"}))
  expect(await screen.findByText("验证码 482731")).toBeInTheDocument()
})
```

- [ ] **Step 2: Run and verify failure**
  Run: `cd web; npm test -- --run src/pages/AdminFlows.test.tsx`.
  Expected: fails because routes and pages do not exist.
- [ ] **Step 3: Implement the minimum**: typed fetch with `credentials: include`, error-envelope decoding, in-memory CSRF token refreshed by session API, protected routing, compact responsive navigation with Lucide icons, overview cards/recent events, masked/filterable messages, OTP status/copy/void/source actions, device create/disable/rotate/revoke, API client creation, webhook rules, audit list, retention/parser settings, and one-time credential dialog. Use `EventSource` to invalidate React Query caches; do not place explanatory marketing copy in the UI.
- [ ] **Step 4: Run and verify pass**
  Run: `cd web; npm test -- --run; npm run typecheck; npm run build`.
  Expected: all interaction tests pass, TypeScript exits `0`, and `web/dist/index.html` plus hashed assets are produced.
- [ ] **Step 5: Commit**: `git add web && git commit -m "feat: add responsive administration ui"`.

### Task 10: Static Hosting, Docker Compose, and End-to-End Acceptance

**Files:** Create `deploy/Dockerfile`, `deploy/entrypoint.sh`, `deploy/compose.yml`, `deploy/Caddyfile`, `deploy/.env.example`, `deploy/README.md`, `tests/conftest.py`, `tests/test_phase1_e2e.py`; modify `backend/app/main.py`.

- [ ] **Step 1: Write the failing acceptance test**

```python
def test_phase1_live_flow(api):
    device = api.create_shortcut_device()
    first = api.ingest(device, client_message_id="e2e-1", body="您的验证码是 482731")
    assert first.status_code == 201
    assert api.ingest(device, client_message_id="e2e-1", body="您的验证码是 482731").json()["duplicate"] is True
    client = api.create_claim_client(device_id=device.id)
    assert api.claim(client).json()["code"] == "482731"
    assert api.claim(client).status_code == 404
```

- [ ] **Step 2: Run and verify failure**
  Run: `docker compose -f deploy/compose.yml config`.
  Expected: fails because `deploy/compose.yml` does not exist.
- [ ] **Step 3: Implement the minimum**: use a Node build stage for `web/dist` and Python 3.12 runtime for FastAPI; copy assets to `backend/app/static`; entrypoint runs `alembic upgrade head` then one Uvicorn worker. Compose defines `app`, PostgreSQL 16 `db` with named volume and health checks, and profile-gated `caddy`; fail configuration when database/admin/master-key secrets are empty. Serve SPA fallback outside `/api`. Add root `tests/conftest.py` with shared `client`, authenticated `admin_client`, and live `api` fixtures used by cross-component contract tests. Document secret-generation commands, first start, HTTPS production profile, `pg_dump`/`pg_restore`, migration rollback procedure, and persistence verification.
- [ ] **Step 4: Run and verify pass**
  Run: `docker compose -f deploy/compose.yml config; docker compose -f deploy/compose.yml up -d --build; .\backend\.venv\Scripts\python.exe -m pytest tests/test_phase1_e2e.py -q; docker compose -f deploy/compose.yml restart; .\backend\.venv\Scripts\python.exe -m pytest tests/test_phase1_e2e.py -q`.
  Expected: config succeeds; `db` and `app` become healthy; migration is current; first ingest/duplicate/claim/second-claim behavior passes; admin static UI loads; data survives restart; application logs contain none of the test body, OTP, Bearer token, HMAC secret, session token, or master key.
- [ ] **Step 5: Commit**: `git add deploy backend/app/main.py tests/conftest.py tests/test_phase1_e2e.py && git commit -m "feat: ship compose deployment and phase1 acceptance"`.

## Final Verification Gate

Run from repository root:

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend/tests tests -q
.\backend\.venv\Scripts\python.exe -m ruff check backend/app backend/tests tests
.\backend\.venv\Scripts\python.exe -m mypy backend/app
npm --prefix web test -- --run
npm --prefix web run typecheck
npm --prefix web run build
docker compose -f deploy/compose.yml config
```

Expected: every command exits `0`; PostgreSQL-only concurrency tests are not skipped; no secret-bearing fixture appears in captured logs; all error responses match the fixed envelope; the generated OpenAPI includes admin, device ingest, OTP claim, health, and SSE routes.

Manual Compose acceptance uses a newly generated master key, administrator password, device credential, and API-client token; verifies HTTPS through Caddy; repeats ingest with identical and changed bodies; checks masked/revealed UI behavior and audit rows; restarts Compose and confirms persistence. Completion is withheld if this live chain has not been run.
