# Ve 2.3.4 integration patch

This patch adds a second, independently switched SMS relay channel to
`WingCH/Ve` without replacing Ve's notification logger or Bark/AI pipeline.

## Exact upstream baseline

- Repository: `https://github.com/WingCH/Ve`
- Commit: `a69204defc3e7fb6b14638f32dc8225807655aa9`
- Version: `2.3.4`
- Tag object/commit recorded by the release: `c71140e99930692c3b71f58b4717759f9813de0c`
- License: GPL-3.0; see `../COPYING`

The patch is generated against the exact commit above. Do not assume it applies
to another Ve release without reviewing the resulting diff.

## What the patch changes

1. Adds `Security` to `Tweak/Core/Makefile` because the relay client uses
   `SecRandomCopyBytes`.
2. Imports `Manager/SMSRelayClient.h` from `VeCore.m`.
3. Keeps the existing `BBServer publishBulletin:destinations:` hook installed
   when either Ve logging or SMS Relay is enabled.
4. Sends only bulletins whose section ID is `com.apple.MobileSMS`.
5. Guards private selectors with `respondsToSelector:` and class checks.
6. Prefers `publicationDate`, then falls back to `date`.
7. Leaves Ve's own enable switch, blocked senders, log retention, Bark, and AI
   paths unchanged.

The relay client still enforces its own enabled flag, HTTPS-only endpoint,
strict plist permissions, UUID, 32-byte HMAC secret, nonce, replay-resistant
request timestamp, idempotency key, concurrency limit, and redacted logging.

## Apply from a clean Ve checkout

```sh
git clone https://github.com/WingCH/Ve.git
cd Ve
git checkout a69204defc3e7fb6b14638f32dc8225807655aa9

# Replace this with the absolute path to sms-relay-hub.
RELAY_ROOT=/path/to/sms-relay-hub

cp "$RELAY_ROOT/clients/ve-tweak/SMSRelayClient.h" Manager/
cp "$RELAY_ROOT/clients/ve-tweak/SMSRelayClient.m" Manager/
git apply "$RELAY_ROOT/clients/ve-tweak/patches/ve-core-2.3.4-sms-relay.patch"

git diff --check
git diff --stat
```

`VeCore_FILES` uses the existing `../../Manager/*.m` wildcard, so no filename
needs to be added to the Makefile after copying the two client files.

## Build

Build the package variant that matches the device's jailbreak:

```sh
# Standard rootless, upstream Theos
make clean package FINALPACKAGE=1 THEOS_PACKAGE_SCHEME=rootless

# Relaxin / RootHide, roothide/theos
THEOS=/absolute/path/to/roothide-theos \
  make clean package FINALPACKAGE=1 THEOS_PACKAGE_SCHEME=roothide
```

The two package schemes are not interchangeable. Check the generated `.deb`
architecture before installation.

## Installation rules

- Install either the standalone `VeSMSRelay` tweak or the integrated VeCore
  variant, never both. Both hook
  `BBServer publishBulletin:destinations:` and would double-process events.
- Keep the existing Ve injection filter and package metadata when integrating.
- Configure `/var/mobile/Library/Preferences/com.smsrelay.ve.plist` with mode
  `0600`; see `../README.md`.
- Respring after installing or changing the relay plist.

## Privacy boundary

Do not use Ve 2.3.4's "full API capture" feature as the relay transport. That
feature intentionally can retain unredacted API endpoints, request/response
headers, tokens, and bodies. The SMS relay path does not read or copy those
captures, but an operator who enables full capture must treat the resulting
local logs as secret material.

## Acceptance boundary

A successful `git apply` or source-level contract check is not a device test.
Real acceptance still requires an authorized jailbroken iPhone, the matching
Theos/RootHide toolchain, a successful build/install, a real inbound SMS, and a
reachable HTTPS relay with enrolled credentials.
