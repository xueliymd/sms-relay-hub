# Root Module Runtime and Startup Fix

## Goal

Make the Root module start reliably within the shell contract harness and ship only the two Android ARM runtimes requested by the user: `runtime-linux-arm64` and `runtime-linux-arm`.

## Scope

- Preserve Magisk, KernelSU, and APatch installer compatibility.
- Remove `runtime-linux-amd64` and `runtime-linux-386` from source artifacts and archives.
- Keep strict configuration validation and marker-based hot reload behavior.
- Fix the slow service startup path that prevents the reader loop from being reached.

## Verification Gates

1. Existing `service-marker` behavior test must pass with a short timeout.
2. Shell reader and package contract tests must pass.
3. Native Go tests and both ARM runtime builds must pass.
4. The generated archive must contain only the ARM64 and 32-bit ARM runtime entries.
5. No `x86` or `x86_64` runtime files or installer branches may remain.

