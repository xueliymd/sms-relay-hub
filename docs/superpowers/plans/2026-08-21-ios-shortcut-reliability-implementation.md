# iOS Shortcut Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the iPhone Messages automation contract explicit, fail closed, diagnosable, and repeatable for foreground and lock-screen testing against the fresh production Relay Hub.

**Architecture:** Keep iOS as a standard Shortcuts personal automation. The shortcut receives the triggered message, builds the exact server payload, sends a Bearer-authenticated HTTPS request, and displays only a localized outcome. The repository provides reference code, action-by-action Chinese instructions, error mapping, and a smoke script; it does not generate an unsigned or falsely claim an Apple-importable `.shortcut` binary.

**Tech Stack:** iOS Shortcuts, HTTPS JSON, Node.js built-ins/tests, PowerShell 7 smoke, FastAPI ingest contract.

---

### Task 1: Lock Payload and Error Contract

**Files:**
- Modify: `ios-shortcut/build-payload.js`
- Test: `ios-shortcut/tests/build-payload.test.js`

- [ ] Add failing tests for normalized base URL, stable message ID, bearer header, and server result classification for `201`, duplicate `200`, `401`, `413`, `422`, `503`, and network failure.
- [ ] Run the focused Node test and confirm the new assertions fail against the current helper.
- [ ] Implement the smallest pure helpers: `classifyResponse(status, body)`, `classifyNetworkError(error)`, and strict HTTPS URL normalization without placing credentials in JSON or query parameters.
- [ ] Re-run focused tests, then the complete `node --test tests/*.test.js` suite.

### Task 2: Make the Chinese Shortcut Definition Executable

**Files:**
- Modify: `ios-shortcut/shortcut-definition.md`
- Modify: `ios-shortcut/README.md`
- Modify: `ios-shortcut/VALIDATION.md`
- Test: `ios-shortcut/tests/artifacts.test.js`

- [ ] Add exact action names and variable mapping for `Shortcut Input` content, sender, and date; preserve the original message date separately from `received_at`.
- [ ] Document the exact JSON dictionary, request headers, and result branches using Chinese labels that match iOS Shortcuts.
- [ ] Document lock-screen prerequisites, “运行前询问” behavior, network permission, and the fact that iOS may still require confirmation.
- [ ] Add a redacted troubleshooting table for `201`, `200 duplicate`, `401`, `413`, `422`, `503`, timeout, and DNS/TLS failures.
- [ ] Verify the example remains disabled and secret-free with the artifact test.

### Task 3: Harden the Smoke and Server Deployment Handoff

**Files:**
- Modify: `ios-shortcut/smoke.ps1`
- Modify: `deploy/README.md`
- Test: `ios-shortcut/tests/artifacts.test.js`

- [ ] Make smoke output status/error codes only, accept a fresh device Bearer token from an environment variable, and never echo request bodies or tokens.
- [ ] Add a production smoke checklist requiring a fresh PostgreSQL volume, `/api/v1/health`, `/api/v1/admin/runtime`, HTTPS certificate, and a new iOS device enrollment.
- [ ] State explicitly that `relay-dev-live.db`, `debug/preview.db`, Android queues, Root state, and test artifacts are not copied to production.
- [ ] Run Node tests, backend tests, and a PowerShell syntax check.

### Task 4: Real iPhone Acceptance

**Files:**
- Update: `ios-shortcut/VALIDATION.md` with a redacted acceptance record template.

- [ ] Connect the iPhone and record only iOS version, device model, lock state, and whether the automation asks for confirmation.
- [ ] Create a fresh `ios_shortcut` device on the production server and place its Bearer credential only in the shortcut.
- [ ] Send one controlled test SMS in foreground and one while locked; record HTTP outcome and platform message ID without recording Token or full OTP.
- [ ] Repeat the same trigger and confirm `200 duplicate=true`; change only the body with the same ID and confirm `409 idempotency_conflict` using the smoke harness or server logs.
- [ ] Mark any iOS system-policy limitation separately from application defects.
