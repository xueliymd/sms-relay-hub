# Dual-SIM Subscription Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correctly attribute incoming Android SMS messages to SIM1 or SIM2 across OEM broadcast variants.

**Architecture:** Normalize broadcast hints in a pure Kotlin resolver, use `SmsMessage` subscription reflection only as fallback, resolve authoritative SIM metadata through `SubscriptionManager`, and prevent ambiguous cache entries from persisting. Keep the backend contract unchanged.

**Tech Stack:** Kotlin 2.0.21, Android SDK 35, Kotlin test, Gradle 8.9.

---

### Task 1: Add failing hint-resolution tests

**Files:**
- Create: `android/app/src/test/kotlin/io/smsrelay/collector/SmsSubscriptionHintResolverTest.kt`
- Modify: `android/app/src/test/kotlin/io/smsrelay/collector/SmsRelayProtocolTest.kt`

- [ ] Test missing extras falling back to message subscription ID.
- [ ] Test string and array OEM extras.
- [ ] Test resolved snapshot slot overriding a conflicting raw slot.
- [ ] Run focused tests and confirm failure before implementation.

### Task 2: Implement robust subscription resolution

**Files:**
- Create: `android/app/src/main/java/io/smsrelay/collector/SmsSubscriptionHintResolver.kt`
- Modify: `android/app/src/main/java/io/smsrelay/collector/SmsReceivedReceiver.kt`
- Modify: `android/app/src/main/java/io/smsrelay/collector/SmsRelayPayload.kt`

- [ ] Parse standard and OEM broadcast keys safely.
- [ ] Add reflective `SmsMessage` subscription fallback.
- [ ] Prefer snapshot slot over raw slot.
- [ ] Run focused tests and confirm pass.

### Task 3: Prevent stale ambiguous SIM cache entries

**Files:**
- Modify: `android/app/src/main/java/io/smsrelay/collector/SmsRelayRuntime.kt`
- Modify: `android/app/src/test/kotlin/io/smsrelay/collector/SubscriptionSnapshotCacheTest.kt`

- [ ] Add a failing slot-only cache regression test.
- [ ] Cache only complete slot+subscription mappings.
- [ ] Run subscription tests and confirm pass.

### Task 4: Build and emulator verification

**Files:**
- Modify: `android/app/build.gradle.kts`
- Modify: `android/README.md`

- [ ] Increment Android version to `1.0.9`.
- [ ] Run unit tests, lint and `assembleDebug` with Gradle 8.9.
- [ ] Install the APK on `emulator-5554`.
- [ ] Launch the app and verify package, foreground service and basic runtime state.
- [ ] Record the limitation that a single-SIM emulator cannot prove radio-level dual-SIM delivery.
