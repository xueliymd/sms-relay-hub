# Online Update Implementation Plan

1. Preserve the installed APK signing identity in a controlled external release keystore.
2. Add release catalog validation and public latest/history endpoints with backend tests.
3. Add a PowerShell publisher that verifies APK identity, writes versioned history, and atomically updates the catalog.
4. Add Android release parsing, policy checks, periodic checks, notification, secure download, certificate verification, and FileProvider installation.
5. Add manual update controls and safe status reporting to MainActivity.
6. Build a same-certificate release APK, publish it as the next version, and verify catalog, download, upgrade, and cancellation behavior on the emulator.
