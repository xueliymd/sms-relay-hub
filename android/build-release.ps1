param(
    [string]$SigningProperties = "$env:USERPROFILE\.sms-relay\signing\release-signing.properties",
    [string]$GradleCommand = "gradle"
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $SigningProperties)) {
    throw "Signing properties not found: $SigningProperties"
}
foreach ($line in Get-Content -LiteralPath $SigningProperties) {
    if ($line -match '^([A-Z0-9_]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
    }
}

& $GradleCommand -p $PSScriptRoot clean testDebugUnitTest lintDebug assembleRelease --no-daemon
if ($LASTEXITCODE -ne 0) { throw 'Android release build failed' }

$apk = Join-Path $PSScriptRoot 'app/build/outputs/apk/release/app-release.apk'
if (-not (Test-Path -LiteralPath $apk)) { throw "Signed release APK not found: $apk" }
Write-Output $apk
