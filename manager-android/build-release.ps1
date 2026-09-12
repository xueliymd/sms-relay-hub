param(
    [string]$SigningProperties = "$env:USERPROFILE\.sms-relay\signing\release-signing.properties",
    [string]$GradleCommand = "gradle"
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $SigningProperties)) {
    throw "Signing properties not found: $SigningProperties"
}

$legacyMapping = @{
    RELAY_SIGNING_STORE_FILE = 'RELAY_MANAGER_SIGNING_STORE_FILE'
    RELAY_SIGNING_STORE_PASSWORD = 'RELAY_MANAGER_SIGNING_STORE_PASSWORD'
    RELAY_SIGNING_KEY_ALIAS = 'RELAY_MANAGER_SIGNING_KEY_ALIAS'
    RELAY_SIGNING_KEY_PASSWORD = 'RELAY_MANAGER_SIGNING_KEY_PASSWORD'
}
foreach ($line in Get-Content -LiteralPath $SigningProperties) {
    if ($line -notmatch '^([A-Z0-9_]+)=(.*)$') { continue }
    $name = $Matches[1]
    $value = $Matches[2]
    [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    if ($legacyMapping.ContainsKey($name)) {
        [Environment]::SetEnvironmentVariable($legacyMapping[$name], $value, 'Process')
    }
}

foreach ($required in @(
    'RELAY_MANAGER_SIGNING_STORE_FILE',
    'RELAY_MANAGER_SIGNING_STORE_PASSWORD',
    'RELAY_MANAGER_SIGNING_KEY_ALIAS',
    'RELAY_MANAGER_SIGNING_KEY_PASSWORD'
)) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($required, 'Process'))) {
        throw "Release signing variable is missing: $required"
    }
}

& $GradleCommand -p $PSScriptRoot clean testDebugUnitTest lintDebug assembleRelease --no-daemon
if ($LASTEXITCODE -ne 0) { throw 'Manager Android release build failed' }

$apk = Join-Path $PSScriptRoot 'app/build/outputs/apk/release/app-release.apk'
if (-not (Test-Path -LiteralPath $apk)) { throw "Signed release APK not found: $apk" }
Write-Output $apk
