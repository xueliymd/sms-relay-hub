# Ve SMS Relay Tweak

This directory contains a source-level Ve/Theos integration for the SMS Relay Hub.
The client signs the existing ingest protocol with HMAC-SHA256 and sends only
MobileSMS bulletin events. The hook delegates all validation and networking to
SMSRelayClient.

## Configuration

The preference file is:

`/var/mobile/Library/Preferences/com.smsrelay.ve.plist`

Use this disabled schema before enrollment. Empty credentials are intentionally
not accepted while Enabled is true:

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

The enrolled values must be supplied by the relay administrator. BaseURL must
be HTTPS with no user info, query, or fragment. DeviceID must be a UUID.
HMACSecret must be unpadded base64url that decodes to exactly 32 bytes. Zero,
repeated-byte, and placeholder secrets are rejected. Keep the plist private:

```sh
chmod 0600 /var/mobile/Library/Preferences/com.smsrelay.ve.plist
```

The client opens this exact path with symlink following disabled, accepts only
a regular file owned by the SpringBoard effective user, rejects all group/other
permission bits, and caps the plist at 16384 bytes. It parses the bytes read
from that same open descriptor, so replacing the path cannot redirect an
already-started configuration read.

## Build and install

Build with the user's jailbreak and Ve/Theos toolchain on a matching SDK:

```sh
make clean package
make package install
```

The Makefile targets arm64 and arm64e, links Foundation and Security, uses
Objective-C ARC, and targets SpringBoard. Install the generated package with
the normal package manager for the device. Restart SpringBoard (respring)
after installing or changing the tweak.

The injection filter and private BulletinBoard selector are toolchain/device
integration points. Keep the package's existing Ve filter metadata when adding
these source files; this repository does not claim that injection works on a
real phone without external acceptance.

## Rotation, revocation, and recovery

For rotation, set Enabled to false, respring, replace BaseURL, DeviceID, and
HMACSecret atomically, set file mode 0600, enable the client, and respring
again. Register the new device and secret on the server before enabling it.

For revocation, disable the client and revoke the old device credential on the
server. Remove or replace the old secret from the plist. A revoked credential
must not be retried as a new credential. To recover, provision a new UUID and
secret, verify the endpoint, then enable and respring.

Status logging is redacted and numeric-only: configuration/input failures are
fixed category messages and HTTP results contain only a numeric status. The
client never logs the secret, signature, URL, request/response body, sender, or
SMS body.

One shared ephemeral session uses a 15-second request timeout and a 30-second
resource timeout. At most four relay requests may be in flight. Additional
messages fail closed with a fixed status category until a task completes.

## Acceptance boundary

PowerShell source-level contract checks and HMAC vectors are the repository
verification. Ve injection, the exact private BulletinBoard selector on the
target iOS build, a real iPhone build/install, and real network acceptance are
external acceptance tasks. They require the user's authorized jailbreak/Ve
toolchain, a test device, enrolled credentials, and a reachable HTTPS relay.
