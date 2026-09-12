# Air724 WebSocket OTP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Android/Root/iPhone message ingestion on the existing immediate HTTP paths while adding an authenticated WebSocket delivery channel for Air724 Lua scripts, with HTTP Claim used only to recover a missed code after a disconnect.

**Architecture:** Message ingestion remains transactional in `Store.ingest()`. After a newly committed message creates an available OTP, the API publishes that OTP to an in-process WebSocket broker. Air724 authenticates with a Script API token in the first WebSocket frame, subscribes with an explicit receiving phone number and optional device/sender/keyword filters, and receives only matching OTPs created after the subscription watermark. Reconnection performs one HTTP Claim fallback before resuming live WebSocket delivery. The broker is intentionally single-process; the deployment audit will require one application worker until a shared Redis/PostgreSQL notification broker is introduced.

**Tech Stack:** Python 3.12, FastAPI WebSocket, SQLAlchemy 2, PostgreSQL/SQLite test database, pytest/TestClient, React 18/Vitest, Air724 LuatOS Lua client files, Caddy WebSocket reverse proxy.

---

## Task 1: Lock the OTP claim and subscription semantics with failing tests

**Files:**
- Modify: `backend/tests/test_relay.py`

- [x] Add a regression test proving that claiming the newest matching OTP does not delete an older still-valid matching OTP.
- [x] Add a WebSocket test proving that a missing/invalid Script API token is rejected before subscription.
- [x] Add a WebSocket test proving that a valid Script API token can subscribe by normalized phone number.
- [x] Add a WebSocket test proving that only a newly ingested matching OTP is pushed and unrelated phone/device/sender/keyword messages are not pushed.
- [x] Add a WebSocket test proving duplicate HTTP ingest does not produce a second WebSocket OTP event.
- [x] Run the relay claim/WebSocket regression tests.

## Task 2: Expose read-only OTP delivery candidates from the store

**Files:**
- Modify: `backend/app/services/store.py`
- Modify: `backend/tests/test_relay.py`

- [x] Add a store method that resolves one message's available, unexpired OTP and decrypted routing fields after the ingest transaction has committed.
- [x] Return only delivery-safe fields: `otp_id`, `message_id`, normalized receiving phone lookup input, sender, code, server `created_at`, and `expires_at`; do not return the SMS body.
- [x] Remove the `stale_ids` bulk deletion from `Store.claim()` so non-selected valid OTP rows remain available until claimed or expired.
- [x] Run the focused claim regression test and confirm it passes.

## Task 3: Implement the authenticated in-process WebSocket broker

**Files:**
- Create: `backend/app/services/otp_websocket.py`
- Create: `backend/tests/test_otp_websocket.py`

- [x] Write unit tests for subscription normalization, exact device filtering, sender substring filtering, keyword matching against the source SMS body at publish time, and connection removal.
- [x] Implement an async broker with per-connection subscriptions, an async lock, bounded send timeout, dead-connection cleanup, and no retained backlog.
- [x] Publish to matching sockets concurrently so a slow subscriber does not block a fast subscriber.
- [x] Run the broker unit tests and confirm GREEN.

## Task 4: Add `/api/v1/ws/otp` and publish after successful ingest

**Files:**
- Modify: `backend/app/api/relay.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_relay.py`

- [x] Add `WebSocket` imports and a `/api/v1/ws/otp` endpoint.
- [x] Require the first client JSON frame to be `{"type":"auth","token":"<SCRIPT_API_TOKEN>"}` and authenticate it with `store.authenticate_api_client()`.
- [x] Require the next frame to be `{"type":"subscribe","phone_number":"..."}` with optional `device_id`, `sender`, and `keyword`; reject missing phone numbers and malformed UUIDs.
- [x] Reply with `auth_ok`, then `subscribed` including a server timestamp; support lightweight `ping`/`pong` frames without database work.
- [x] After non-duplicate HTTP ingest and iPhone shortcut ingest, load the committed OTP delivery candidate and publish it through the broker.
- [x] Do not publish duplicate/idempotent replays and do not mark an OTP claimed from WebSocket delivery.
- [x] Run the WebSocket integration tests and the existing claim/concurrency tests.

## Task 5: Add the Air724 Lua WebSocket client and disconnect-only HTTP fallback

**Files:**
- Create: `clients/air724/README.md`
- Create: `clients/air724/config.example.lua`
- Create: `clients/air724/main.lua`
- Create: `clients/air724/tests/test_contract.py`

- [x] Verify the available Air724/LuatOS WebSocket and HTTP API names from an official OpenLuat source before fixing the runtime calls in `main.lua`.
- [x] Write contract tests that assert the client uses `/api/v1/ws/otp`, authenticates with the Script API token, subscribes with an explicit phone number, deduplicates by `otp_id`, reconnects with bounded backoff, uses a bounded OTP watermark, and calls `/api/v1/otp/claim` only from the reconnect recovery path.
- [x] Implement the smallest Lua client satisfying that contract, with heartbeat, reconnect, one fallback Claim per reconnect cycle, and an application callback receiving the exact code and metadata.
- [x] Document the required configuration keys and the distinction between Script API token and device credentials.
- [x] Re-run the Air724 contract tests and confirm GREEN.

## Task 6: Remove the SIM card-board active adapter and present Air724 as a script consumer

**Files:**
- Modify: `backend/app/services/store.py`
- Modify: `backend/tests/test_relay.py`
- Modify: `web/src/App.tsx`
- Modify: `web/src/App.test.tsx`
- Delete: `clients/sim-board/config.example.json`
- Delete: `clients/sim-board/polling.py`
- Delete: `clients/sim-board/normalize.py`
- Delete: `clients/sim-board/webhook.py`
- Delete: `clients/sim-board/README.md`
- Delete: `clients/sim-board/drivers/README.md`
- Delete: `clients/sim-board/tests/test_normalize.py`

- [x] Change frontend/backend tests so the Add Device list no longer offers `SIM 卡板` or `sim_board`.
- [x] Remove `sim_board` from `DEVICE_CONTRACTS`, the frontend Device union, client options, and enrollment test matrix.
- [x] Delete the obsolete generic SIM-board bridge package.
- [x] Add Air724 to Add Device as a managed script consumer; keep it outside the SMS-ingest device contracts.
- [x] Add Air724 WebSocket/HTTP recovery details next to the Script API credential panel and tutorial.
- [x] Re-run the focused backend and frontend tests and confirm GREEN.

## Task 7: Update active tutorials and downloadable client assets

**Files:**
- Modify: `docs/DEVICE_TUTORIAL.md`
- Modify: `README.md`
- Modify: `web/src/App.tsx`
- Modify: `web/src/App.test.tsx`
- Create: `web/public/assets/downloads/air724-otp-websocket/main.lua`
- Create: `web/public/assets/downloads/air724-otp-websocket/config.example.lua`
- Create: `web/public/assets/downloads/air724-otp-websocket/README.md`

- [x] Add a dedicated `合宙 724 取码脚本` tutorial/download section separate from Android, Root, and iPhone ingestion tutorials.
- [x] Show the browser-derived `ws://` or `wss://` endpoint, the HTTP Claim fallback endpoint, Script API token, receiving phone number, and optional filters.
- [x] State that Android app, Root module, and iPhone keep HTTP ingest and are unaffected.
- [x] Copy the verified Lua client files into the public download directory.
- [x] Run frontend tests, typecheck, and `npm run build`.

## Task 8: Deployment audit for WebSocket production readiness

**Files:**
- Modify: `deploy/Caddyfile`
- Modify: `deploy/compose.yml`
- Modify: `deploy/.env.example`
- Modify: `deploy/README.md`
- Create: `docs/DEPLOYMENT_AUDIT_2026-08-23.md`
- Modify: `backend/tests/test_deploy.py`

- [x] Add deployment tests for WebSocket proxying, one-worker constraint, generated secrets, default/custom ports, persistent PostgreSQL data, downloadable assets, and required environment variables.
- [x] Configure/document WebSocket idle handling and confirm Caddy reverse proxy preserves Upgrade connections.
- [x] Keep the application at one worker while using the in-process broker; document Redis/PostgreSQL pub-sub as the prerequisite for horizontal workers.
- [x] Audit bootstrap generation for `ADMIN_TOKEN`, `DATA_ENCRYPTION_KEY`, PostgreSQL password/URL, and Script API token creation behavior.
- [x] Record every verified item, corrected item, residual risk, and server command in the deployment audit report.
- [x] Run deployment tests again and confirm GREEN.

## Task 9: Full verification and local preview

**Files:**
- Review all modified files above.

- [x] Run `backend\.venv\Scripts\python.exe -m pytest -q` from `backend/`.
- [x] Run `backend\.venv\Scripts\python.exe -m ruff check app tests ..\clients\air724` from `backend/`.
- [x] Run `npm test -- --run` from `web/`.
- [x] Run `npm run typecheck` and `npm run build` from `web/`.
- [x] Run `git diff --check` and inspect `git status --short` without reverting unrelated user changes.
- [x] Rebuild and inspect the deployment ZIP; local preview restart remains environment-specific.
- [x] Report hardware testing honestly: automated protocol tests are complete; a real Air724 WebSocket session remains a hardware acceptance step unless a connected board is available.

## Task 10: Validate generic API scripts separately from Air724

**Files:**
- Create: `clients/script_api/otp_client.py`
- Create: `clients/script_api/README.md`
- Create: `clients/script_api/tests/test_otp_client.py`
- Create: `backend/tests/test_script_api_network.py`
- Modify: `backend/app/services/store.py`
- Modify: `backend/tests/test_relay.py`
- Modify: `deploy/package.ps1`
- Modify: `backend/tests/test_deploy.py`

- [x] Add a generic Python Script API client using the same authenticated WebSocket protocol; it is independent of Air724 hardware.
- [x] Validate a real TCP/Uvicorn chain: create Script API token, authenticate, subscribe by phone, ingest an HMAC SMS, receive the exact OTP and matching `message_id`, then recover once through HTTP Claim.
- [x] Verify duplicate/idempotent HTTP ingest does not emit a second WebSocket OTP frame.
- [x] Align HTTP Claim sender filtering with WebSocket substring matching and add a regression test.
- [x] Include the generic client and its network test in the deployment package validation.
- [x] Latest real local delivery measurement: `28.4 ms` from authenticated HTTP ingest to generic script WebSocket receipt.
