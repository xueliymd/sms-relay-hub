# Android Root And iOS Ve Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide installable/configurable adapter artifacts for Magisk/KernelSU/APatch Android devices and WingCH/Ve-based jailbroken iPhones while reusing the existing HMAC ingest protocol.

**Architecture:** Keep the server protocol in `backend/app/api/relay.py`. Add a small shell-based Android root module that reads a normalized event source and posts signed JSON, with configuration in `/data/adb/sms-relay/config.json`. Add a Ve integration patch and Objective-C helper that filters `com.apple.MobileSMS` bulletin events and delegates signed HTTP upload to a configurable client. Both packages fail closed when disabled or missing credentials.

**Tech Stack:** POSIX shell, `curl`, `jq`-free JSON escaping helper, Objective-C/Logos-compatible source, HMAC-SHA256, existing Python contract fixtures.

---

### Task 1: Define adapter protocol fixtures

**Files:**
- Create: `clients/root-module/tests/fixtures/message.json`
- Create: `clients/ve-tweak/tests/fixtures/message.json`
- Create: `clients/tests/signature_vectors.json`
- Create: `clients/tests/verify_vectors.ps1`

- [ ] **Step 1:** Add deterministic JSON and HMAC vectors for `root_module` and `ve_tweak`, including nonce, timestamp, source, and idempotency key.
- [ ] **Step 2:** Run the verifier and confirm it fails because adapter scripts are absent.

### Task 2: Build the Android Root module skeleton

**Files:**
- Create: `clients/root-module/module.prop`
- Create: `clients/root-module/service.sh`
- Create: `clients/root-module/action.sh`
- Create: `clients/root-module/system/bin/sms-relay-upload`
- Create: `clients/root-module/config.example.json`
- Create: `clients/root-module/README.md`

- [ ] **Step 1:** Implement configuration validation for HTTPS base URL, UUID device ID, base64url HMAC secret, and source `root_module`.
- [ ] **Step 2:** Implement a signed upload command that accepts normalized JSON from a platform-specific reader and emits only status codes.
- [ ] **Step 3:** Add a polling loop with lockfile, exponential backoff, and no message-body logging. Leave the SMS database reader behind a documented adapter command so vendor ROM/schema differences do not corrupt messages.
- [ ] **Step 4:** Document Magisk, KernelSU, and APatch installation, configuration, start/stop, rotation, revoke, and uninstall rollback.
- [ ] **Step 5:** Run shell syntax and signature-vector tests.

### Task 3: Add the iOS Ve adapter patch

**Files:**
- Create: `clients/ve-tweak/SMSRelayClient.h`
- Create: `clients/ve-tweak/SMSRelayClient.m`
- Create: `clients/ve-tweak/VeSMSRelay.xm`
- Create: `clients/ve-tweak/Makefile`
- Create: `clients/ve-tweak/README.md`

- [ ] **Step 1:** Implement an Objective-C client that builds the canonical body, signs timestamp/nonce/body hash with HMAC-SHA256, and sends HTTPS JSON.
- [ ] **Step 2:** Hook only `com.apple.MobileSMS` bulletin events, extract sender/body/date, and use a stable hash as the idempotency key.
- [ ] **Step 3:** Add a preference/config schema for URL, device ID, secret, enabled flag, and redacted status logging. Never log the token or message body.
- [ ] **Step 4:** Document building against the user’s jailbreak/Ve toolchain, installing the package, restarting SpringBoard, and recovering from revoked credentials.
- [ ] **Step 5:** Run source-level contract checks; mark injection and real-iPhone behavior as external acceptance.

### Task 4: Verify adapter artifacts

- [ ] **Step 1:** Run PowerShell shell/static checks and HMAC vectors.
- [ ] **Step 2:** Verify every artifact points to `root_module` or `ve_tweak` and no placeholder secret is accepted.
- [ ] **Step 3:** Record that APK/Root/Ve real-device installation remains environment-dependent.
