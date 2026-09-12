param(
    [string]$Output = "deploy/dist/sms-relay-hub-update.zip"
)

$ErrorActionPreference = 'Stop'
$packageScript = Join-Path $PSScriptRoot 'package.ps1'
& $packageScript -Output $Output
