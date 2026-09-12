# Phase 1 iOS Shortcut Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a secure, manually reproducible iOS Messages personal automation that posts versioned SMS JSON to SMS Relay Hub, plus device-independent contract tests and real-iPhone acceptance instructions.

**Architecture:** The repository stores an auditable Shortcut action definition rather than claiming to generate an Apple-signed `.shortcut`. Node.js builds deterministic reference payloads; the real Shortcut recreates the same fields and sends them over HTTPS with a per-device Bearer token and one idempotency key shared by header and body. Backend pytest and a PowerShell smoke test verify HTTP behavior without representing themselves as iOS tests.

**Tech Stack:** iOS Shortcuts, HTTPS/JSON, Node.js 20 built-ins, PowerShell 7 + `curl.exe`, FastAPI `TestClient`, pytest.

---

**Cross-plan contracts:** Backend work provides authenticated `client`/`admin_client` pytest fixtures through root `tests/conftest.py`; `POST /api/v1/admin/devices`, `POST /api/v1/admin/devices/{device_id}/rotate-credential`, and `DELETE /api/v1/admin/devices/{device_id}`; and `POST /api/v1/ingest/messages`. First ingest returns `201`; exact duplicate returns `200` with `duplicate: true`; same key with a different body returns `409`/`idempotency_conflict`; invalid, rotated, or revoked tokens return `401`; errors use `{"error":{"code":"string","message":"string"}}`.

### Task 1: Directory And Fail-Closed Security Configuration

**Files:**
- Create: `ios-shortcut/config.example.json`
- Create: `ios-shortcut/tests/artifacts.test.js`
- Modify: `.gitignore`

- [ ] **Step 1: Write the failing artifact-security test**

```javascript
// ios-shortcut/tests/artifacts.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

test('example config is HTTPS, disabled, and secret-free', () => {
  const raw = fs.readFileSync(path.join(root, 'config.example.json'), 'utf8');
  const config = JSON.parse(raw);
  assert.equal(config.base_url, 'https://relay.invalid');
  assert.equal(config.device_token, 'NOT_ENROLLED');
  assert.doesNotMatch(raw, /Bearer\s+[A-Za-z0-9_-]{32,}/);
});
```

- [ ] **Step 2: Run `node --test ios-shortcut/tests/artifacts.test.js`; expect FAIL with `ENOENT: config.example.json`.**
- [ ] **Step 3: Create the exact non-secret configuration and ignore local/debug material.**

```json
{"schema_version":1,"base_url":"https://relay.invalid","device_token":"NOT_ENROLLED","shortcut_version":"1.0.0"}
```

Append to `.gitignore`: `ios-shortcut/config.local.json` and `*.shortcut`; `debug/*` is already ignored except for `debug/README.md`. The repository must never contain a configured or unsigned Shortcut binary.

- [ ] **Step 4: Re-run the Node test; expect `1` passing test and exit code `0`.**
- [ ] **Step 5: Commit implementation work with `git add .gitignore ios-shortcut/config.example.json ios-shortcut/tests/artifacts.test.js && git commit -m "chore: add safe iOS shortcut configuration"`.**

### Task 2: Shortcut JSON Payload Construction

**Files:**
- Create: `ios-shortcut/build-payload.js`
- Create: `ios-shortcut/tests/build-payload.test.js`

- [ ] **Step 1: Write tests for field mapping, stable IDs, and sensitive-field exclusion.**

```javascript
const test = require('node:test');
const assert = require('node:assert/strict');
const { buildPayload } = require('../build-payload');
const input = { sender: 'Authorized Relay Test', body: 'Your relay access code is 482731', messageDate: '2026-08-15T01:02:03Z', receivedAt: '2026-08-15T09:02:05+08:00' };
test('builds the iOS schema deterministically', () => {
  const a = buildPayload(input); const b = buildPayload(input);
  assert.deepEqual(a, b); assert.equal(a.schema_version, 1);
  assert.equal(a.source, 'ios_shortcut'); assert.equal(a.client_message_id, b.client_message_id);
  assert.equal(a.metadata.shortcut_version, '1.0.0');
  assert.equal('device_token' in a, false); assert.equal('sim_slot' in a, false);
});
```

- [ ] **Step 2: Run `node --test ios-shortcut/tests/build-payload.test.js`; expect FAIL with `MODULE_NOT_FOUND`.**
- [ ] **Step 3: Implement the reference builder using the same canonical text the Shortcut will hash.**

```javascript
const { createHash } = require('node:crypto');
function buildPayload({ sender, body, messageDate, receivedAt }) {
  for (const [key, value] of Object.entries({ sender, body, messageDate, receivedAt })) if (!value) throw new Error(`${key} is required`);
  const digest = createHash('sha256').update(`ios-shortcut-v1\n${sender}\n${messageDate}\n${body}`, 'utf8').digest('hex');
  return { schema_version: 1, client_message_id: `ios-shortcut-v1:${digest}`, received_at: receivedAt, sender, body, source: 'ios_shortcut', metadata: { shortcut_version: '1.0.0' } };
}
module.exports = { buildPayload };
```

- [ ] **Step 4: Add tests for missing fields and two different message dates producing different IDs; expect `3` passing tests.**
- [ ] **Step 5: Commit with `git add ios-shortcut/build-payload.js ios-shortcut/tests/build-payload.test.js && git commit -m "feat: define iOS shortcut payload"`.**

### Task 3: Bearer And Idempotent Request Definition

**Files:**
- Modify: `ios-shortcut/build-payload.js`
- Modify: `ios-shortcut/tests/build-payload.test.js`
- Create: `ios-shortcut/request-contract.md`

- [ ] **Step 1: Add failing tests requiring HTTPS, rejecting `NOT_ENROLLED`, and making header/body IDs identical.**

```javascript
const { buildHeaders, ingestUrl } = require('../build-payload');
test('builds the Bearer request contract', () => {
  const id = buildPayload(input).client_message_id;
  assert.equal(ingestUrl('https://relay.test'), 'https://relay.test/api/v1/ingest/messages');
  assert.deepEqual(buildHeaders('test-token-value', id), { Authorization: 'Bearer test-token-value', 'Content-Type': 'application/json', 'X-Idempotency-Key': id });
  assert.throws(() => ingestUrl('http://relay.test'), /HTTPS/);
  assert.throws(() => buildHeaders('NOT_ENROLLED', id), /enrolled/);
});
```

- [ ] **Step 2: Run the test; expect FAIL because `buildHeaders` and `ingestUrl` are not exported.**
- [ ] **Step 3: Implement both helpers, stripping one trailing slash and never logging arguments; document `201`, duplicate `200`, conflict `409`, and token `401` in `request-contract.md`.**
- [ ] **Step 4: Run `node --test ios-shortcut/tests/artifacts.test.js ios-shortcut/tests/build-payload.test.js`; expect all tests to pass with exit code `0`.**
- [ ] **Step 5: Commit with `git add ios-shortcut/build-payload.js ios-shortcut/tests/build-payload.test.js ios-shortcut/request-contract.md && git commit -m "feat: define iOS Bearer ingest request"`.**

### Task 4: Personal Automation Manual Build Documentation

**Files:**
- Create: `ios-shortcut/shortcut-definition.md`
- Create: `ios-shortcut/README.md`
- Modify: `ios-shortcut/tests/artifacts.test.js`

- [ ] **Step 1: Extend the artifact test to require both documents to contain `Get Details of Messages`, `Current Date`, `SHA-256`, `Get Contents of URL`, `Run Immediately`, `Ask Before Running`, `X-Idempotency-Key`, and `Authorization`.**
- [ ] **Step 2: Run the test; expect FAIL because both documents are absent.**
- [ ] **Step 3: Write `shortcut-definition.md` as an ordered action list:** config Text actions (`RelayBaseURL`, `DeviceToken`); fail closed unless HTTPS/enrolled; read `Content`, `Sender`, and stable message date from Shortcut Input; capture Current Date for `received_at`; SHA-256 `ios-shortcut-v1\nsender\nmessageDate\nbody`; build Dictionary fields from Task 2; POST JSON with Bearer and the same ID in `X-Idempotency-Key`; display only sanitized success/failure text.
- [ ] **Step 4: Write `README.md` with exact enrollment and automation steps:** Admin UI creates an `ios_shortcut` device and displays its token once; paste it only into the local Shortcut; select Messages personal automation; restrict by authorized sender and/or `验证码`, `校验码`, `动态码`, `OTP`, `code`; add `Run Shortcut` and pass Shortcut Input; select `Run Immediately` or disable `Ask Before Running` when that UI is shown.
- [ ] **Step 5: Document rotation/revocation and limitations:** rotation immediately invalidates the old token; deletion revokes the current token; configured Shortcuts expose the token to the device owner and must not be shared; labels/options vary by iOS version; silent locked-screen execution is not guaranteed; there is no durable retry queue; SIM slot is unavailable; overlapping automations require the stable message-date hash; trusted HTTPS is mandatory.
- [ ] **Step 6: Run the artifact tests; expect all documentation assertions to pass, then commit with `git add ios-shortcut/shortcut-definition.md ios-shortcut/README.md ios-shortcut/tests/artifacts.test.js && git commit -m "docs: add iOS shortcut build guide"`.**

### Task 5: Backend Contract Tests And PowerShell Smoke Test

**Files:**
- Create: `tests/contract/test_ios_shortcut_backend.py`
- Create: `ios-shortcut/smoke.ps1`

- [ ] **Step 1: Write pytest contract tests using only HTTP calls and the shared `client`/`admin_client` fixtures:** create an iOS device; assert first ingest `201`; repeat exact payload/key and assert `200`, `duplicate is True`, and unchanged `message_id`; change body with the same key and assert `409` plus `error.code == "idempotency_conflict"`; rotate and revoke tokens and assert old/current tokens return `401` plus `invalid_device_token`.
- [ ] **Step 2: Run `docker compose run --rm app python -m pytest tests/contract/test_ios_shortcut_backend.py -q`; expect contract failures until the backend plan implements the agreed routes, never an iOS-success claim.**
- [ ] **Step 3: Add a redaction assertion with `caplog`: require an `ingest.accepted` event while excluding the raw token, sender, body, OTP, and idempotency key from captured logs.**
- [ ] **Step 4: Implement `smoke.ps1` around `curl.exe`; require `SMS_RELAY_BASE_URL` and `SMS_RELAY_DEVICE_TOKEN`, reject non-HTTPS URLs, generate one payload/key, send it twice, then send changed body with the same key.**

```powershell
if (-not $env:SMS_RELAY_BASE_URL.StartsWith('https://')) { throw 'SMS_RELAY_BASE_URL must use HTTPS' }
$clientId = "ios-shortcut-smoke:$([guid]::NewGuid())"
# The script writes JSON to a temporary file, calls curl.exe with Bearer and X-Idempotency-Key, and removes the file in finally.
```

- [ ] **Step 5: Run `powershell -NoProfile -File ios-shortcut/smoke.ps1`; expect `first=201 duplicate=false`, `second=200 duplicate=true`, `conflict=409 code=idempotency_conflict`, with no token or SMS body printed.**
- [ ] **Step 6: Commit with `git add tests/contract/test_ios_shortcut_backend.py ios-shortcut/smoke.ps1 && git commit -m "test: cover iOS shortcut ingest contract"`.**

### Task 6: Real iPhone Acceptance And Troubleshooting

**Files:**
- Create: `ios-shortcut/VALIDATION.md`
- Modify: `ios-shortcut/README.md`

- [ ] **Step 1: Write a real-device checklist requiring a trusted HTTPS deployment, an enrolled test-device token, manual Shortcut execution with sender `Authorized Relay Test` and body `Your relay access code is 482731`, and dashboard confirmation that one message and one available access code were created.**
- [ ] **Step 2: Require a real Messages automation test from an authorized sender, including foreground and locked-screen observations; expected backend result is one accepted message, while any system confirmation prompt is recorded as an iOS limitation rather than hidden.**
- [ ] **Step 3: Require failure-path checks:** disable network and confirm visible failure with no claimed durable retry; rotate token and confirm old-token `401` before updating the Shortcut; delete the test device and confirm current-token `401`.
- [ ] **Step 4: State that screenshots, console exports, and temporary payload captures belong only under `debug/ios-shortcut/`, must redact sender/body/OTP/token, and must not be committed.**
- [ ] **Step 5: Run final automation checks: `node --test ios-shortcut/tests/artifacts.test.js ios-shortcut/tests/build-payload.test.js`, `docker compose run --rm app python -m pytest tests/contract/test_ios_shortcut_backend.py -q`, and `powershell -NoProfile -File ios-shortcut/smoke.ps1`; expect zero test failures and the three smoke status lines from Task 5.**
- [ ] **Step 6: Perform the iPhone checklist. If no real iPhone is available or the personal automation has not received a real SMS, report Phase 1 iOS acceptance as incomplete; backend tests alone are not completion evidence. Commit verified docs with `git add ios-shortcut/VALIDATION.md ios-shortcut/README.md && git commit -m "docs: record iOS shortcut acceptance"`.**
