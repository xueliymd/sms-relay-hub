# Root Dual-SIM And Local Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make dual-SIM phone mapping complete, reduce SMS visibility latency, and add an on-demand loopback-only Root module configuration console.

**Architecture:** Persist observed SIM slots during ingest without overwriting manual phone mappings, render one management row per slot, and refresh the console in the background. Extend the existing Go runtime with a token-protected loopback HTTP server and make the POSIX worker consume wake/config markers with interruptible waits.

**Tech Stack:** FastAPI, SQLAlchemy, React, Vitest, Go `net/http`, POSIX shell, APatch/KernelSU/Magisk module packaging.

---

### Task 1: Persist every observed SIM slot

**Files:**
- Modify: `backend/tests/test_relay.py`
- Modify: `backend/app/services/store.py`

- [ ] Add a failing ingest test where slot 0/subscription 5 and slot 1/subscription 6 have no phone number, then assert two SIM profiles exist.
- [ ] Add a failing test proving later automatic metadata cannot replace a manually saved phone.
- [ ] Run the focused backend tests and record the expected failures.
- [ ] Add an internal observed-profile upsert called before receiving identity resolution.
- [ ] Add a history reconciliation method for existing messages and call it when listing profiles.
- [ ] Run the focused and complete backend suites.

### Task 2: Render and edit both SIM phone numbers

**Files:**
- Modify: `web/src/App.test.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/styles.css`

- [ ] Add failing tests for two profile rows, independent slot-specific PUT requests, and selecting the first unused slot.
- [ ] Add a failing test for background message refresh without reconnecting.
- [ ] Run focused Vitest and record RED.
- [ ] Display SIM labels and subscription hints only inside the device phone dialog.
- [ ] Replace the hard-coded slot 0 creation path with first-unused-slot selection.
- [ ] Add a quiet 2-second refresh effect that is cancelled on disconnect/unmount.
- [ ] Run focused and complete web tests, typecheck, and build.

### Task 3: Make the Root worker event-aware

**Files:**
- Modify: `clients/root-module/tests/behavior.sh`
- Modify: `clients/root-module/service.sh`
- Modify: `clients/root-module/config.example.json`
- Modify: `clients/root-module/README.md`

- [ ] Add failing shell cases proving a wake marker interrupts the wait and config changes are reparsed.
- [ ] Run the POSIX harness and record RED.
- [ ] Add `wait_for_poll_or_wake` using one-second sleeps and marker consumption.
- [ ] Reparse and validate config at the start of each loop after a config marker.
- [ ] Change the example/default poll interval to 3 seconds.
- [ ] Run the complete POSIX harness.

### Task 4: Add the on-demand loopback configuration console

**Files:**
- Modify: `clients/root-module/native/runtime/main_test.go`
- Modify: `clients/root-module/native/runtime/main.go`
- Modify: `clients/root-module/action.sh`
- Modify: `clients/root-module/tests/reader_contract.sh`
- Modify: `clients/root-module/README.md`

- [ ] Add failing Go tests for redacted GET config, authenticated POST update, invalid/public bind rejection, atomic permissions, and idle timeout.
- [ ] Add failing shell contract tests for no-argument/`web` Action launching `127.0.0.1:35000` while existing commands remain unchanged.
- [ ] Run Go and shell focused tests and record RED.
- [ ] Implement `web` and `web-token` runtime subcommands with random session token and loopback-only HTTP serving.
- [ ] Embed a compact static HTML/CSS/JS configuration form; never return the current HMAC secret.
- [ ] Atomically replace the config and create `run/config.changed` after validation.
- [ ] Start the server on demand from Action, open it through Android `am start`, and reuse an already-running server.
- [ ] Run Go tests and complete Root shell harnesses.

### Task 5: Package and verify on the connected phone

**Files:**
- Modify: `clients/root-module/module.prop`
- Generated: `clients/root-module/bin/runtime-linux-*`
- Generated: `clients/root-module/dist/SMS-Relay-Root-Adapter-0.2.5.zip`

- [ ] Bump the module to 0.2.5 and rebuild all four runtime architectures.
- [ ] Run backend, web, Go, Root POSIX, typecheck, build, and `git diff --check` verification.
- [ ] Package the module and verify required entries and SHA-256.
- [ ] Copy the ZIP to `/sdcard/Download/` for manual APatch installation.
- [ ] After installation, verify self-test, Action console, hot configuration update, both SIM profile rows, and two fresh SMS uploads.
- [ ] Measure fresh `created_at - received_at` delays and report actual evidence without claiming unsupported devices are verified.
