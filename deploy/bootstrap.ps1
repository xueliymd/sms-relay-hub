$ErrorActionPreference = 'Stop'

$envFile = Join-Path $PSScriptRoot '.env'
$defaults = [ordered]@{
    RELAY_SITE = ':80'
    RELAY_DOMAIN = ''
    HTTP_PORT = '39300'
    HTTPS_PORT = '39301'
    APP_PORT = '39200'
    PLATFORM_VERSION = '1.1.0'
    PUBLIC_BASE_URL = ''
    ADMIN_TOKEN = ''
    ADMIN_USERNAME = 'admin'
    ADMIN_PASSWORD = ''
    DATA_ENCRYPTION_KEY = ''
    POSTGRES_DB = 'sms_relay'
    POSTGRES_USER = 'sms_relay'
    POSTGRES_PASSWORD = ''
    DATABASE_URL = ''
    RETENTION_DAYS = '7'
    RETENTION_INTERVAL_SECONDS = '3600'
    OTP_TTL_SECONDS = '600'
    DEVICE_COMMAND_PENDING_TTL_SECONDS = '600'
    DEVICE_COMMAND_RESULT_TTL_SECONDS = '1800'
    CLAIMED_OTP_RETENTION_SECONDS = '86400'
    HMAC_SKEW_SECONDS = '300'
}

function New-UrlSafeSecret {
    param([int]$Bytes = 32)
    $buffer = New-Object byte[] $Bytes
    [Security.Cryptography.RandomNumberGenerator]::Fill($buffer)
    return [Convert]::ToBase64String($buffer).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Read-EnvFile {
    param([string]$Path)
    $values = [ordered]@{}
    if (-not (Test-Path -LiteralPath $Path)) { return $values }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $values[$Matches[1]] = $Matches[2]
        }
    }
    return $values
}

$values = Read-EnvFile -Path $envFile
foreach ($name in $defaults.Keys) {
    if (-not $values.Contains($name)) { $values[$name] = $defaults[$name] }
}

# Do not overwrite existing non-empty credentials.
$generated = [ordered]@{}
if ([string]::IsNullOrWhiteSpace($values.ADMIN_PASSWORD)) { $values.ADMIN_PASSWORD = New-UrlSafeSecret; $generated.ADMIN_PASSWORD = $values.ADMIN_PASSWORD }
if ([string]::IsNullOrWhiteSpace($values.ADMIN_TOKEN)) { $values.ADMIN_TOKEN = New-UrlSafeSecret; $generated.ADMIN_TOKEN = $values.ADMIN_TOKEN }
if ([string]::IsNullOrWhiteSpace($values.DATA_ENCRYPTION_KEY)) { $values.DATA_ENCRYPTION_KEY = New-UrlSafeSecret; $generated.DATA_ENCRYPTION_KEY = $values.DATA_ENCRYPTION_KEY }
if ([string]::IsNullOrWhiteSpace($values.POSTGRES_PASSWORD)) { $values.POSTGRES_PASSWORD = New-UrlSafeSecret; $generated.POSTGRES_PASSWORD = $values.POSTGRES_PASSWORD }
if ([string]::IsNullOrWhiteSpace($values.DATABASE_URL)) {
    $db = $values.POSTGRES_DB
    $user = $values.POSTGRES_USER
    $password = [Uri]::EscapeDataString($values.POSTGRES_PASSWORD)
    $values.DATABASE_URL = "postgresql+psycopg://${user}:${password}@postgres:5432/${db}"
}

$lines = foreach ($name in $defaults.Keys) { "${name}=$($values[$name])" }
$extraNames = @($values.Keys | Where-Object { $_ -notin $defaults.Keys } | Sort-Object)
$lines += foreach ($name in $extraNames) { "${name}=$($values[$name])" }
# Write UTF-8 with no BOM. Every UTF8 encoding value of the text-writing cmdlets emits a
# BOM on Windows PowerShell 5.1, and a BOM lands on the first key, so RELAY_SITE parses
# as a different name: deploy/preflight.sh then reports "RELAY_SITE is missing or empty"
# and Caddy gets no site address. WriteAllLines uses the platform newline and appends a
# trailing one, matching what the cmdlet produced. backend/tests/test_deploy.py asserts
# the cmdlet form never comes back, so do not name it here either.
[System.IO.File]::WriteAllLines($envFile, [string[]]$lines, [System.Text.UTF8Encoding]::new($false))
Write-Output "Wrote $envFile. Existing non-empty credentials were preserved."
Write-Output "Configured ports: HTTP=$($values.HTTP_PORT), HTTPS=$($values.HTTPS_PORT), internal app=$($values.APP_PORT)."
Write-Output "Keep RELAY_SITE=:80. Set RELAY_DOMAIN to your real DNS name to enable HTTPS while keeping the IP:HTTP_PORT entry."
if ($generated.Count -gt 0) {
    Write-Output "Generated administrator credentials (store these securely):"
    Write-Output "  Username: $($values.ADMIN_USERNAME)"
    if ($generated.Contains('ADMIN_PASSWORD')) { Write-Output "  Password: $($values.ADMIN_PASSWORD)" }
    if ($generated.Contains('ADMIN_TOKEN')) { Write-Output "  Recovery token: $($values.ADMIN_TOKEN)" }
    Write-Output "The generated DATA_ENCRYPTION_KEY and database password are in $envFile."
}
