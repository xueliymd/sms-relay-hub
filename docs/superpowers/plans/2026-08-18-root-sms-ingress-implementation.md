# Root SMS Ingress And Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Android Root adapter into a pure Magisk/KernelSU/APatch module that reads new SMS records on the connected Xiaomi device, persists and uploads them reliably, resolves SIM/phone identity, and exposes routable messages in the platform.

**Architecture:** Keep the existing HMAC/spool/quarantine contract, but replace the external `reader_command` requirement with a module-owned static Linux/arm64 runtime. The runtime uses the Android Content Provider first, queries each field separately so SMS text cannot corrupt delimiters, and falls back to discovered SQLite `sms` schemas when a compatible SQLite runtime is available. The backend resolves a receiving phone snapshot at ingest, stores it encrypted plus a keyed lookup hash, and exposes filters and read/OTP state without exposing secrets.

**Tech Stack:** POSIX shell, Go 1.25.1, `crypto/hmac`, `crypto/sha256`, `encoding/json`, `modernc.org/sqlite v1.56.0` for the optional database reader, FastAPI/SQLAlchemy/Alembic, React/TypeScript, PowerShell contract harnesses, ADB on the connected `ac38745a` device.

**Workspace rule:** Do not commit, stage, reset, checkout, clean, or revert existing worktree changes. Use the existing `feature/phase-1` worktree and preserve unrelated user/agent edits.

---

### Task 1: Freeze the Root runtime contract with failing tests

**Files:**
- Create: `clients/root-module/native/runtime/go.mod`
- Create: `clients/root-module/native/runtime/main.go`
- Create: `clients/root-module/native/runtime/main_test.go`
- Create: `clients/root-module/tests/reader_contract.sh`
- Modify: `clients/root-module/config.example.json`
- Modify: `clients/root-module/README.md`

- [ ] **Step 1: Write failing Go tests for field parsing and stable events**

Add tests before implementation. The tests must use an injected command runner, never a live phone:

```go
func TestParseSingleFieldPreservesNewlinesDelimitersAndUnicode(t *testing.T) {
    raw := []byte("Row: 0 body=验证码=482731, 保留逗号\n第二行\n")
    got, err := parseSingleField(raw, "body")
    if err != nil { t.Fatal(err) }
    if got != "验证码=482731, 保留逗号\n第二行" { t.Fatalf("got %q", got) }
}

func TestProviderEventIDUsesSourceRowIdentity(t *testing.T) {
    first := eventID("provider", 15615, 1786863717139)
    second := eventID("provider", 15615, 1786863717139)
    if first != second || first != "root:provider:15615:1786863717139" { t.Fatalf("unstable id: %s %s", first, second) }
}

func TestInitialSyncAdvancesCursorWithoutSpoolingHistoricalRows(t *testing.T) {
    spool := t.TempDir()
    cursor := filepath.Join(t.TempDir(), "provider.cursor")
    runner := &fakeRunner{listIDs: []int64{15613, 15614, 15615}}
    reader := providerReader{runner: runner, cursorPath: cursor}
    if err := reader.scan(context.Background(), spool, "new_only"); err != nil { t.Fatal(err) }
    entries, err := os.ReadDir(spool)
    if err != nil { t.Fatal(err) }
    if len(entries) != 0 { t.Fatalf("historical rows were spooled: %d", len(entries)) }
    raw, err := os.ReadFile(cursor)
    if err != nil { t.Fatal(err) }
    if string(raw) != "15615\n" { t.Fatalf("cursor=%q", raw) }
}

func TestSigningMatchesBackendVector(t *testing.T) {
    vector := loadSignatureVector(t, "../../../tests/signature_vectors.json", "root_module_fixture_v1")
    secret, err := decodeSecret(vector.Secret)
    if err != nil { t.Fatal(err) }
    got := sign(secret, vector.Timestamp, vector.Nonce, []byte(vector.Body))
    if got != vector.Signature { t.Fatalf("signature=%q want=%q", got, vector.Signature) }
}
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run:

```powershell
Push-Location clients/root-module/native/runtime; go test ./...; Pop-Location
```

Expected: FAIL because `parseSingleField`, `eventID`, cursor handling, and the runtime command implementation do not exist.

- [ ] **Step 3: Add a strict Root config contract test**

`reader_contract.sh` must reject the old external-reader configuration and accept only the new schema:

```json
{
  "schema_version": 2,
  "enabled": false,
  "base_url": "https://relay.example.invalid",
  "device_id": "",
  "hmac_secret": "",
  "source": "root_module",
  "android_user_id": 0,
  "initial_sync_mode": "new_only",
  "poll_interval_seconds": 15,
  "max_backoff_seconds": 300
}
```

The test must assert that `reader_command`, unknown fields, `initial_sync_mode=all`, negative user IDs, and missing runtime binaries are rejected. It must not print the secret or SMS body.

- [ ] **Step 4: Implement the smallest Go primitives**

Implement `parseSingleField`, `eventID`, strict base64url secret validation, HMAC-SHA256 signing, random nonce generation, and JSON event serialization. `parseSingleField` must require exactly one `Row: 0 <field>=` prefix, remove only the command's final line ending, and preserve all other bytes.

Pin the runtime module exactly:

```go
module sms-relay-root-runtime

go 1.25.1

require modernc.org/sqlite v1.56.0
```

- [ ] **Step 5: Run the focused tests to verify the primitives pass**

Run:

```powershell
Push-Location clients/root-module/native/runtime; go test ./... -run 'TestParseSingleField|TestProviderEventID|TestSigning'; Pop-Location
```

Expected: PASS with no network, ADB, or Android dependency.

---

### Task 2: Implement the provider-first SMS reader and SIM snapshot

**Files:**
- Modify: `clients/root-module/native/runtime/main.go`
- Modify: `clients/root-module/native/runtime/main_test.go`
- Create: `clients/root-module/native/runtime/sqlite_fallback.go`
- Create: `clients/root-module/native/runtime/sqlite_stub.go`
- Create: `clients/root-module/native/runtime/sqlite_fallback_test.go`
- Create: `clients/root-module/native/runtime/testdata/content-query/`
- Modify: `clients/root-module/config.example.json`
- Modify: `clients/root-module/README.md`

- [ ] **Step 1: Add failing provider-reader tests**

Cover these exact cases with a fake `CommandRunner`:

- `_id` listing sorted ascending and filtered by the persisted cursor;
- body containing `,`, `=`, CRLF, LF, Chinese characters and a literal `Row: 1` substring;
- missing optional `sub_id`, `sim_id`, `subscription_id`, `carrier_name`, and `number` columns;
- malformed date and command failure returning a non-success status without writing a partial spool file;
- a new installation with `initial_sync_mode=new_only` advancing to the current maximum `_id` without importing old messages;
- a later SMS producing `root:provider:15616:1786863717139.json` atomically.

Expected event shape:

```json
{
  "schema_version": 1,
  "client_message_id": "root:provider:15616:1786863717139",
  "received_at": "2026-08-18T00:00:00Z",
  "sender": "synthetic sender",
  "body": "fixture body",
  "source": "root_module",
  "sim_slot": 0,
  "metadata": {
    "source_message_id": "15616",
    "subscription_id": 5,
    "carrier_name": "fixture carrier"
  }
}
```

Real test fixtures must contain non-sensitive synthetic values only.

- [ ] **Step 2: Run the provider tests to capture RED**

Run:

```powershell
Push-Location clients/root-module/native/runtime; go test ./... -run 'TestProvider|TestCursor|TestSpool'; Pop-Location
```

Expected: FAIL because no provider command adapter or cursor store exists.

- [ ] **Step 3: Implement provider queries without delimiter-dependent parsing**

The runtime must execute `/system/bin/content` with argument arrays, never a shell string:

```text
content query --uri content://sms/inbox --user 0 --projection _id --where "_id>15615" --sort "date ASC,_id ASC"
```

For each new `_id`, issue separate single-row queries for `_id`, `address`, `body`, `date`, and optional fields. Parse the body as the bytes after the exact `Row: 0 body=` prefix. Probe optional SIM data with `sub_id`, then `sim_id`, then `subscription_id`; omit unsupported fields instead of failing the SMS.

Persist the cursor under `/data/adb/sms-relay/reader/provider.cursor` with a temporary file, mode `0600`, and atomic rename. Write each JSON event to a `0700` spool directory using `root-provider-15616.tmp` followed by `rename(root:provider:15616:1786863717139.json)`. Never overwrite an existing spool event.

- [ ] **Step 4: Add subscription/phone snapshot probing**

Probe `content://telephony/siminfo` only when the SMS row has a subscription or SIM hint. Read candidate columns one at a time: `_id`, `slot_index`, `sim_id`, `subscription_id`, `number`, `carrier_name`, `display_name`. Match by subscription ID or SIM ID. If two rows match or the number is empty, omit the ambiguous value and retain the SMS event.

- [ ] **Step 5: Implement the database fallback with explicit schema discovery**

Use `modernc.org/sqlite v1.56.0` only in the runtime build tagged `sqlite_fallback`. The package is CGo-free and officially supports Linux arm64, not Android GOOS; build it as a static `GOOS=linux GOARCH=arm64` binary and verify it on Android before shipping. The fallback must:

1. Discover only `/data/user_de/{android_user_id}/com.android.providers.telephony/databases` and `/data/user/{android_user_id}/com.android.providers.telephony/databases`;
2. require `mmssms.db` or a database containing a table named `sms`;
3. inspect `pragma table_info(sms)` and select only columns present;
4. require `_id`, `address`, `body`, `date`; treat `sub_id`, `sim_id`, `type`, `deleted` as optional;
5. read only incoming rows (`type=1` when present, `deleted=0` when present);
6. use the same event ID, cursor, spool, SIM mapping, and JSON serializer as Provider mode.

If the fallback binary is unavailable or its Android smoke test fails, the module must report `database_fallback_unavailable`; it must not silently switch to a fragile path parser.

- [ ] **Step 6: Run the complete runtime test set**

Run:

```powershell
Push-Location clients/root-module/native/runtime; go test ./... -count=1; Pop-Location
```

Expected: PASS for synthetic Provider and SQLite fixtures. The test output must not contain fixture SMS bodies outside explicit assertion failure text.

---

### Task 3: Replace OpenSSL dependency and integrate the owned reader into the module

**Files:**
- Create: `clients/root-module/native/build.ps1`
- Create: `clients/root-module/customize.sh`
- Generated, not committed: `clients/root-module/bin/runtime-linux-arm64`
- Generated, not committed: `clients/root-module/bin/runtime-linux-arm`
- Generated, not committed: `clients/root-module/bin/runtime-linux-amd64`
- Generated, not committed: `clients/root-module/bin/runtime-linux-386`
- Create: `clients/root-module/system/bin/sms-relay-wake`
- Modify: `clients/root-module/system/bin/sms-relay-upload`
- Modify: `clients/root-module/service.sh`
- Modify: `clients/root-module/action.sh`
- Modify: `clients/root-module/tests/behavior.sh`
- Modify: `clients/root-module/tests/reader_contract.sh`
- Modify: `clients/root-module/README.md`

- [ ] **Step 1: Add failing shell integration assertions**

Extend the behavior harness to assert:

- startup succeeds when `openssl` is absent but `sms-relay-runtime` is present;
- startup rejects a missing runtime with `runtime_unavailable`;
- service invokes the owned runtime with `read --spool-dir /data/adb/sms-relay/spool --user-id 0` and never invokes an external `reader_command`;
- a disabled marker stops both the reader and uploader;
- `inotifyd` wake ends the wait early, while timeout performs a reconciliation scan;
- no runtime, uploader, or action output contains the HMAC secret, sender, or body.

Run before implementation:

```powershell
sh clients/root-module/tests/behavior.sh
```

Expected: FAIL for the new runtime contract.

- [ ] **Step 2: Build the static runtime for supported Android ABIs**

`build.ps1` must produce `bin/runtime-linux-arm64`, `bin/runtime-linux-arm`, `bin/runtime-linux-amd64`, and `bin/runtime-linux-386`. Build arm64 with SQLite fallback and the other Provider-capable ABIs without that tag:

```powershell
$env:CGO_ENABLED = "0"
$env:GOOS = "linux"
foreach ($abi in @("arm64", "arm", "amd64", "386")) {
    $env:GOARCH = $abi
    $tags = if ($abi -eq "arm64") { @("-tags", "sqlite_fallback") } else { @() }
    & go build @tags -trimpath -buildvcs=false -ldflags "-s -w" -o "bin/runtime-linux-$abi" ./native/runtime
    if ($LASTEXITCODE -ne 0) { throw "runtime build failed for $abi" }
}
```

The install script maps `ro.product.cpu.abilist` to one binary, copies it into `$MODPATH/system/bin/sms-relay-runtime`, and applies mode `0755` before the module overlay becomes active. The module must fail closed for an ABI with no matching binary. The first real-device build target is `arm64`, matching `ac38745a`.

- [ ] **Step 3: Remove the OpenSSL runtime requirement**

Change `sms-relay-upload` to call `/system/bin/sms-relay-runtime` for `check-config`, `nonce`, `sha256`, and `hmac`. Secret material must be passed through the config file descriptor or a Root-only temporary file, never as a process argument. Keep the existing strict JSON body validation and raw-body hashing unchanged.

Update the uploader dependency check to require `curl`, `awk`, `grep`, `sed`, `tr`, `wc`, `mktemp`, `od`, `dd`, `date`, `rm`, and the runtime, but not `openssl`.

- [ ] **Step 4: Make `service.sh` own the reader lifecycle**

Remove `reader_command` parsing and validation. After processing inflight claims, invoke:

```text
sms-relay-runtime read --state-dir /data/adb/sms-relay/reader --spool-dir /data/adb/sms-relay/spool --user-id 0
```

After each pass, start `inotifyd` on discovered Telephony database paths and wait until either a change event or `poll_interval_seconds` expires. Always run the next reconciliation scan after either wake path. Child watcher and sleeper PIDs must be tracked and killed in the EXIT trap.

- [ ] **Step 5: Verify shell/runtime integration**

Run:

```powershell
sh clients/root-module/tests/behavior.sh
pwsh -NoProfile -File clients/tests/verify_vectors.ps1 -RepositoryRoot (Get-Location).Path
```

Expected: existing Root behavior cases plus the new no-OpenSSL, owned-reader, wake and redaction cases pass. A printed `SKIP root_posix_runtime` remains an explicit host-environment limitation, not Android evidence.

---

### Task 4: Add server-side receiving identity resolution and message state

**Files:**
- Modify: `backend/app/db/models.py`
- Create: `backend/migrations/versions/20260818_0003_message_receiving_identity.py`
- Modify: `backend/app/core/crypto.py`
- Modify: `backend/app/services/store.py`
- Modify: `backend/app/api/relay.py`
- Modify: `backend/tests/test_relay.py`
- Modify: `backend/tests/test_migrations.py`

- [ ] **Step 1: Write failing backend tests**

Add this complete subscription-priority test first:

```python
def test_ingest_resolves_phone_by_subscription_before_slot(client: TestClient) -> None:
    created = create_device(client, client_type="root_module", auth_kind="hmac")
    device_id = created["device"]["id"]
    secret = created["credential"]["secret"]
    assert client.put(
        f"/api/v1/admin/devices/{device_id}/sim-profiles/0",
        json={"subscription_id": 5, "phone_number": "+15550100005", "source": "manual"},
        headers=ADMIN_HEADERS,
    ).status_code == 200
    assert client.put(
        f"/api/v1/admin/devices/{device_id}/sim-profiles/1",
        json={"subscription_id": 6, "phone_number": "+15550100006", "source": "manual"},
        headers=ADMIN_HEADERS,
    ).status_code == 200
    payload = relay_payload("root-subscription-5", source="root_module")
    payload["sim_slot"] = 1
    payload["metadata"] = {"subscription_id": 5}
    raw, headers = hmac_request(device_id, secret, payload, nonce="root-subscription-priority")
    response = client.post("/api/v1/ingest/messages", content=raw, headers=headers)
    assert response.status_code == 201
    messages = client.get("/api/v1/admin/messages", headers=ADMIN_HEADERS).json()
    message = next(item for item in messages if item["client_message_id"] == "root-subscription-5")
    assert message["receiving_phone_masked"].endswith("0005")
    assert message["receiving_identity_status"] == "resolved"
```

Then add separate tests named `test_ingest_resolves_phone_by_slot_when_subscription_missing`, `test_conflicting_sim_profiles_mark_message_unresolved`, `test_auto_phone_candidate_never_overwrites_manual_profile`, `test_admin_message_filters_by_receiving_phone_sender_keyword_and_slot`, `test_otp_claim_filters_by_receiving_phone_and_text_endpoint_returns_code`, and `test_message_read_state_is_separate_from_otp_claim_state`. Each must assert that the response exposes only a masked receiving phone, never the encrypted/plaintext storage fields or device secret. Use synthetic phone numbers and bodies.

- [ ] **Step 2: Run the backend tests to capture RED**

Run:

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_relay.py -q
```

Expected: FAIL because messages have no receiving identity snapshot, lookup hash, read state, or filtered query parameters.

- [ ] **Step 3: Add encrypted identity snapshot columns and migration**

Add to `MessageModel`:

- `receiving_phone_lookup_hash` (`String(64)`, nullable, indexed);
- `receiving_phone_nonce`, `receiving_phone_ciphertext`, `receiving_phone_key_version`;
- `receiving_identity_status` (`resolved`, `unresolved`, `conflict`);
- `read_at` (`DateTime`, nullable).

The Alembic migration must add these columns with a backwards-compatible nullable default and an index on the lookup hash/status. `FieldCrypto` must expose a keyed lookup digest using the decoded data-encryption key and a domain separator; the digest must not be reversible.

- [ ] **Step 4: Resolve profiles inside the ingest transaction**

Given `metadata.subscription_id`, `sim_slot`, and optional `metadata.phone_number`:

1. lock and load matching profiles for the device;
2. prefer an exact subscription match, then an exact slot match;
3. if multiple matches contain different phone numbers, set `conflict`;
4. if a manual profile exists, never overwrite it with an automatic candidate;
5. if no profile exists and a valid automatic phone is supplied, create an `auto` candidate only after validation;
6. snapshot a resolved phone encrypted with an AAD containing message ID;
7. write the keyed lookup hash and status;
8. leave the message accepted with `unresolved` when no phone is available.

The same resolver must run before OTP extraction so later queries do not need to infer identity from mutable profiles.

- [ ] **Step 5: Add filtered admin APIs and read-state APIs**

Change `GET /api/v1/admin/messages` to accept optional `phone_number`, `device_id`, `sim_slot`, `sender`, `keyword`, `identity_status`, `otp_status`, `read`, `limit`, and `offset`. Phone filtering uses the keyed hash; sender/keyword filtering decrypts only inside the server process. Return `receiving_phone_masked`, `receiving_identity_status`, `sim_slot`, `subscription_id`, `otp_status`, and `read_at` while retaining existing fields.

Add:

```text
POST /api/v1/admin/messages/{message_id}/read
POST /api/v1/admin/messages/{message_id}/unread
GET /api/v1/otp/claim.txt?phone_number=%2B15550100005&sender=Service
```

The existing `POST /api/v1/otp/claim` must also accept `phone_number` and use the same keyed phone lookup as message filtering. The text endpoint uses the same API-client Bearer credential and atomic claim transaction but returns only `text/plain; charset=utf-8` containing the claimed code. The read/unread endpoints change only message read state. OTP `available/claimed/expired` remains controlled by the existing claim lifecycle.

- [ ] **Step 6: Run migration and backend tests**

Run:

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q
```

Expected: all existing tests plus receiving identity, filters, read state, migration upgrade/downgrade and profile conflict tests pass.

---

### Task 5: Add management-table filters and unresolved SIM visibility

**Files:**
- Modify: `web/src/App.tsx`
- Modify: `web/src/App.css`
- Modify: `web/src/App.test.tsx`

- [ ] **Step 1: Write failing frontend tests**

Add tests that:

- render masked receiving phone and `unresolved` status in the message table;
- submit phone, sender, keyword, device, SIM slot and read filters as query parameters;
- distinguish marking a message read from claiming an OTP;
- show an explicit manual SIM mapping action when the message is unresolved;
- never put the masked phone into the update payload as a real phone number.

Run:

```powershell
Push-Location web
npm test -- --run
Pop-Location
```

Expected: FAIL until the message filter state and controls exist.

- [ ] **Step 2: Implement the smallest filter and state controls**

Add a compact filter row above the messages table, use the existing admin token request helper, and reload only the messages query when filters change. Render `receiving_phone_masked` and an unresolved/conflict badge without exposing raw phone data. Use separate icon/text actions for read/unread and OTP claim.

- [ ] **Step 3: Verify frontend tests and production build**

Run:

```powershell
Push-Location web
npm test -- --run
npm run typecheck
npm run build
Pop-Location
```

Expected: PASS with no layout overlap at the existing desktop and mobile test widths.

---

### Task 6: Build, package, and manually install the Root module

**Files:**
- Modify: `clients/root-module/module.prop`
- Modify: `clients/root-module/README.md`
- Create: `clients/root-module/pack.ps1`
- Create: `tests/root_xiaomi_e2e.ps1`
- Generated, not committed: `clients/root-module/dist/`

- [ ] **Step 1: Add packaging checks**

`pack.ps1` must assert that the archive contains `module.prop`, executable `service.sh`, `action.sh`, `system/bin/sms-relay-upload`, `system/bin/sms-relay-wake`, `customize.sh`, and `bin/runtime-linux-{arm64,arm,amd64,386}`. It must reject any embedded APK, `.dex`, or third-party SMS reader command.

- [ ] **Step 2: Build all runtime ABIs and the archive**

Run:

```powershell
pwsh -NoProfile -File clients/root-module/native/build.ps1 -Arch all
pwsh -NoProfile -File clients/root-module/pack.ps1 -Output clients/root-module/dist/sms-relay-root-module.zip
Get-FileHash clients/root-module/dist/sms-relay-root-module.zip -Algorithm SHA256
```

The archive is the artifact to place in the phone's `Download` directory. Do not use ADB package installation; the user installs it from APatch/Magisk/KernelSU manually.

- [ ] **Step 3: Prepare the connected phone without installing through ADB**

Only perform read-only or staging operations through ADB:

```powershell
adb.exe push clients/root-module/dist/sms-relay-root-module.zip /sdcard/Download/sms-relay-root-module.zip
adb.exe shell su -c "ls -l /sdcard/Download/sms-relay-root-module.zip"
```

The module is not considered installed until the user confirms installation and reboot in the Root manager.

- [ ] **Step 4: Execute the real-device checklist after manual install**

`tests/root_xiaomi_e2e.ps1` must verify only non-sensitive facts:

1. module directory exists and selected runtime is executable;
2. `/system/bin/sms-relay-runtime --self-test` succeeds;
3. `sms-relay-upload --check-config` returns `ready` without OpenSSL;
4. `action.sh status` reports enabled/disabled state;
5. a synthetic reader fixture uploads successfully before using a real SMS;
6. after the user sends a test SMS, one new spool event appears and the backend record count increases by one;
7. a module restart does not create a second record;
8. a temporary network failure leaves the event in spool and a later scan clears it;
9. logs contain no sender, body, phone number, secret, or HTTP response body.

The script must stop and report `EXTERNAL_ENDPOINT_REQUIRED` if no real HTTPS backend URL and enrolled Root credentials are configured. It must not use fake credentials or mark that case as passed.

- [ ] **Step 5: Record the redacted evidence**

Write the result to `docs/superpowers/verification/2026-08-18-root-xiaomi-e2e.md` with device model, Android version, module version, test timestamps, status codes, event IDs only, and explicit unverified ROM/ABI rows. Never store the actual SMS, OTP, full phone number, HMAC secret, or server token.

---

### Task 7: Final verification gate for this plan

- [ ] **Step 1: Run backend, frontend, client and Root static gates**

```powershell
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q
Push-Location web; npm test -- --run; npm run typecheck; npm run build; Pop-Location
pwsh -NoProfile -File clients/tests/verify_vectors.ps1 -RepositoryRoot (Get-Location).Path
sh clients/root-module/tests/behavior.sh
Push-Location clients/root-module/native/runtime; go test ./... -count=1; Pop-Location
git diff --check
```

- [ ] **Step 2: Confirm the completion boundary**

This plan is complete only when the connected Xiaomi test reaches all nine real-device checks in Task 6. If the backend endpoint, credentials, or network path is unavailable, report the exact missing external input and leave the result as `source/static verified; real device E2E blocked`, never as a successful SMS relay.

- [ ] **Step 3: Keep later work separate**

Do not claim the non-Root APK, iOS Shortcut PPT, iOS jailbreak plugin, SIM board, CPE, or WiFi hotspot integrations complete in this Root plan. Their implementation plans must reuse the standard event and ingest contract after this plan's real-device path is proven.
