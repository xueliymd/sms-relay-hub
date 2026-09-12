param(
    [string]$SourceKeystore = "$env:USERPROFILE\.android\debug.keystore",
    [string]$DistributedApk = "web/public/assets/downloads/Relay-SMS-Forwarder.apk",
    [string]$DestinationDirectory = "$env:USERPROFILE\.sms-relay\signing"
)

$ErrorActionPreference = 'Stop'

function New-Secret {
    $bytes = New-Object byte[] 36
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Normalize-Fingerprint([string]$Value) {
    $Value.Trim().ToLowerInvariant().Replace(':', '')
}

$projectRoot = Split-Path $PSScriptRoot -Parent
$apkPath = [IO.Path]::GetFullPath((Join-Path $projectRoot $DistributedApk))
if (-not (Test-Path -LiteralPath $SourceKeystore)) { throw "Source keystore not found: $SourceKeystore" }
if (-not (Test-Path -LiteralPath $apkPath)) { throw "Distributed APK not found: $apkPath" }
if (-not $env:JAVA_HOME) { throw 'JAVA_HOME is required' }
if (-not $env:ANDROID_SDK_ROOT) { throw 'ANDROID_SDK_ROOT is required' }

$keytool = Join-Path $env:JAVA_HOME 'bin/keytool.exe'
$apksigner = Get-ChildItem -LiteralPath (Join-Path $env:ANDROID_SDK_ROOT 'build-tools') -Filter apksigner.bat -File -Recurse |
    Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $apksigner) { throw 'apksigner.bat was not found' }

$apkOutput = & $apksigner verify --print-certs $apkPath 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -or $apkOutput -notmatch 'Signer #1 certificate SHA-256 digest:\s*([0-9a-fA-F]+)') {
    throw 'Unable to read the distributed APK signer'
}
$apkFingerprint = Normalize-Fingerprint $Matches[1]

$sourceOutput = & $keytool -list -v -keystore $SourceKeystore -storepass android -alias androiddebugkey -keypass android 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -or $sourceOutput -notmatch 'SHA256:\s*([0-9A-Fa-f:]+)') {
    throw 'Unable to read the source signing key'
}
$sourceFingerprint = Normalize-Fingerprint $Matches[1]
if ($sourceFingerprint -ne $apkFingerprint) {
    throw "Source key does not match the distributed APK signer: $sourceFingerprint != $apkFingerprint"
}

New-Item -ItemType Directory -Force -Path $DestinationDirectory | Out-Null
$DestinationDirectory = (Resolve-Path -LiteralPath $DestinationDirectory).ProviderPath
$destinationKeystore = Join-Path $DestinationDirectory 'relay-release.p12'
$propertiesPath = Join-Path $DestinationDirectory 'release-signing.properties'
if (Test-Path -LiteralPath $destinationKeystore) { throw "Destination keystore already exists: $destinationKeystore" }

$password = New-Secret
& $keytool -importkeystore -noprompt `
    -srckeystore $SourceKeystore -srcstorepass android -srcalias androiddebugkey -srckeypass android `
    -destkeystore $destinationKeystore -deststoretype PKCS12 -deststorepass $password -destkeypass $password -destalias relay
if ($LASTEXITCODE -ne 0) { throw 'Failed to create the controlled release keystore' }

# Every UTF8 value of the Windows PowerShell 5.1 text-writing cmdlets prefixes a
# UTF-8 BOM, which lands on the first key name here. build-release.ps1 happens to
# read this file with Get-Content, which strips it, but a POSIX reader or a Java
# Properties loader would keep those three bytes on RELAY_SIGNING_STORE_FILE and
# drop the key, so write BOM-less UTF-8 through .NET like the deploy scripts do.
$signingProperties = @(
    "RELAY_SIGNING_STORE_FILE=$destinationKeystore"
    "RELAY_SIGNING_STORE_PASSWORD=$password"
    'RELAY_SIGNING_KEY_ALIAS=relay'
    "RELAY_SIGNING_KEY_PASSWORD=$password"
)
[System.IO.File]::WriteAllLines($propertiesPath, [string[]]$signingProperties, [System.Text.UTF8Encoding]::new($false))

if ($IsWindows -or $env:OS -eq 'Windows_NT') {
    $windowsIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    if ([string]::IsNullOrWhiteSpace($windowsIdentity)) { throw 'Unable to resolve the current Windows identity' }
    & icacls $DestinationDirectory /inheritance:r /grant:r "$windowsIdentity`:(OI)(CI)F" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Failed to restrict release signing directory permissions' }
}

Write-Output "Release signing identity secured at $DestinationDirectory"
Write-Output "Signer SHA-256: $apkFingerprint"
Write-Output 'Back up this directory securely. Losing it prevents future in-place updates.'
