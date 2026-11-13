# Ve SMS Relay Tweak

This directory contains the source-level iPhone integration for SMS Relay Hub.
It supports two mutually exclusive deployment modes:

1. A standalone Theos tweak named `VeSMSRelay`.
2. A patch that merges the same relay client into `WingCH/Ve` 2.3.4.

Both modes hook `BBServer publishBulletin:destinations:`, accept only the
`com.apple.MobileSMS` section, and delegate validation/signing/networking to
`SMSRelayClient`. Do not install both modes at the same time.

## Ve 2.3.4 integrated mode

The reviewed upstream baseline is:

- Repository: `https://github.com/WingCH/Ve`
- Commit: `a69204defc3e7fb6b14638f32dc8225807655aa9`
- Version: `2.3.4`
- Upstream tag: `c71140e99930692c3b71f58b4717759f9813de0c`
- License: GPL-3.0; see `COPYING`

The integration patch is:

`patches/ve-core-2.3.4-sms-relay.patch`

Apply it only to the exact upstream commit. The patch keeps Ve's own logging,
blocked-sender, Bark, and AI behavior intact while adding SMS Relay as an
independent channel. It also keeps the hook installed when Ve logging is off
but SMS Relay is on.

Full copy/apply/build instructions are in `patches/README.md`. In short:

```sh
git clone https://github.com/WingCH/Ve.git
cd Ve
git checkout a69204defc3e7fb6b14638f32dc8225807655aa9
cp /path/to/sms-relay-hub/clients/ve-tweak/SMSRelayClient.h Manager/
cp /path/to/sms-relay-hub/clients/ve-tweak/SMSRelayClient.m Manager/
git apply /path/to/sms-relay-hub/clients/ve-tweak/patches/ve-core-2.3.4-sms-relay.patch
```

Build the package variant matching the jailbreak:

```sh
# Standard rootless
make clean package FINALPACKAGE=1 THEOS_PACKAGE_SCHEME=rootless

# Relaxin / RootHide
THEOS=/absolute/path/to/roothide-theos \
  make clean package FINALPACKAGE=1 THEOS_PACKAGE_SCHEME=roothide
```

Standard rootless and RootHide/Relaxin packages are not interchangeable.
Respring after installation or preference changes.

## Independent switch

The relay plist is separate from Ve's own preferences:

`/var/mobile/Library/Preferences/com.smsrelay.ve.plist`

`SMSRelayClient.isEnabled` reads only the relay `Enabled` boolean. This means
Ve logging can be disabled while SMS Relay remains enabled. If both channels
are disabled at SpringBoard start, neither hook is installed; re-enable the
desired channel and respring.

Use this disabled schema before enrollment. Empty credentials are intentionally
rejected whenever `Enabled` is true:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Enabled</key>
    <false/>
    <key>BaseURL</key>
    <string></string>
    <key>DeviceID</key>
    <string></string>
    <key>HMACSecret</key>
    <string></string>
</dict>
</plist>
```

The enrolled values must be supplied by the relay administrator. `BaseURL`
must be HTTPS with no user info, query, or fragment. `DeviceID` must be a UUID.
`HMACSecret` must be unpadded base64url that decodes to exactly 32 bytes. Zero,
repeated-byte, and placeholder secrets are rejected.

Keep the plist private:

```sh
chmod 0600 /var/mobile/Library/Preferences/com.smsrelay.ve.plist
```

The client opens this exact path with symlink following disabled, accepts only
a regular file owned by the SpringBoard effective user, rejects all group/other
permission bits, and caps the plist at 16384 bytes. It parses the bytes read
from the same descriptor, so replacing the path cannot redirect an already
started configuration read.

## Standalone build

Build the standalone package with the user's jailbreak and Theos toolchain:

```sh
make clean package
make package install
```

The Makefile targets arm64 and arm64e, links Foundation and Security, uses
Objective-C ARC, and targets SpringBoard. The upstream injection filter and the
private BulletinBoard selector remain toolchain/device integration points.

## Rotation, revocation, and recovery

For rotation, set `Enabled` to false, respring, replace `BaseURL`, `DeviceID`,
and `HMACSecret` atomically, set mode 0600, enable the client, and respring
again. Register the new device and secret on the server before enabling it.

For revocation, disable the client and revoke the old device credential on the
server. Remove or replace the old secret from the plist. A revoked credential
must not be retried as a new credential. To recover, provision a new UUID and
secret, verify the endpoint, then enable and respring.

## Data flow and privacy

Only the notification sender/display title, body, and notification date are
passed to `SMSRelayClient`. The payload source is `ve_tweak`, uses a stable
sender/body/date message ID, and is signed with HMAC-SHA256 over timestamp,
nonce, and body hash. The client never queries the Messages database, never
uploads attachments, and never logs the secret, signature, URL, request or
response body, sender, or SMS body.

Ve 2.3.4 introduces an optional full API request/response capture that is
explicitly unredacted and can contain tokens, headers, and plaintext bodies.
The relay client does not consume that capture. If full capture is enabled in
Ve itself, treat its local data as secret material and delete it when no longer
needed.

## Verification and limits

PowerShell source-level contract checks and HMAC vectors are the repository
verification. The patch has also been checked against the exact clean Ve 2.3.4
commit. These checks do not prove runtime compatibility with every iOS build.

External acceptance is still required for the exact private
`BBServer publishBulletin:destinations:` selector on the target iOS version,
standalone/integrated tweak injection, real iPhone build/install, and real network acceptance. These require an authorized jailbroken test device, the
matching Theos or RootHide toolchain, enrolled credentials, and a reachable
HTTPS relay. Until those steps are executed, the integration is source-level
verified, not real-device verified.
