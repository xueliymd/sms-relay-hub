param(
    [string]$DestinationDirectory = "$env:USERPROFILE\.sms-relay\signing",
    [string]$Alias = 'relay-manager'
)

$ErrorActionPreference = 'Stop'

function New-Secret {
    $bytes = New-Object byte[] 36
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

if (-not $env:JAVA_HOME) { throw 'JAVA_HOME is required' }
$keytool = Join-Path $env:JAVA_HOME 'bin/keytool.exe'
if (-not (Test-Path -LiteralPath $keytool)) { throw "keytool not found: $keytool" }

New-Item -ItemType Directory -Force -Path $DestinationDirectory | Out-Null
$DestinationDirectory = (Resolve-Path -LiteralPath $DestinationDirectory).ProviderPath
$keystore = Join-Path $DestinationDirectory 'relay-manager-release.p12'
$properties = Join-Path $DestinationDirectory 'manager-release-signing.properties'
if (Test-Path -LiteralPath $keystore) { throw "Destination keystore already exists: $keystore" }
if (Test-Path -LiteralPath $properties) { throw "Signing properties already exist: $properties" }

$password = New-Secret
& $keytool -genkeypair -noprompt `
    -keystore $keystore -storetype PKCS12 -storepass $password -keypass $password `
    -alias $Alias -keyalg RSA -keysize 3072 -validity 36500 `
    -dname 'CN=Relay Hub Manager, OU=Release, O=Relay Hub, L=Local, C=CN'
if ($LASTEXITCODE -ne 0) { throw 'Failed to create manager release keystore' }

# Every UTF8 value of the Windows PowerShell 5.1 text-writing cmdlets prefixes a
# UTF-8 BOM, which lands on the first key name here. build-release.ps1 happens to
# read this file with Get-Content, which strips it, but a POSIX reader or a Java
# Properties loader would keep those three bytes on RELAY_MANAGER_SIGNING_STORE_FILE
# and drop the key, so write BOM-less UTF-8 through .NET like the deploy scripts do.
$signingProperties = @(
    "RELAY_MANAGER_SIGNING_STORE_FILE=$keystore"
    "RELAY_MANAGER_SIGNING_STORE_PASSWORD=$password"
    "RELAY_MANAGER_SIGNING_KEY_ALIAS=$Alias"
    "RELAY_MANAGER_SIGNING_KEY_PASSWORD=$password"
)
[System.IO.File]::WriteAllLines($properties, [string[]]$signingProperties, [System.Text.UTF8Encoding]::new($false))

if ($IsWindows -or $env:OS -eq 'Windows_NT') {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls $DestinationDirectory /inheritance:r /grant:r "$identity`:(OI)(CI)F" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Failed to restrict manager signing directory permissions' }
}

Write-Output "Manager release signing identity created: $properties"
Write-Output 'Back up the keystore and properties securely before publishing.'
