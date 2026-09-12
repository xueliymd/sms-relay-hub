param(
    [Parameter(Mandatory = $true)][string]$ApkPath,
    [Parameter(Mandatory = $true)][string]$Notes,
    [string]$Channel = 'stable',
    [bool]$Mandatory = $false,
    [int]$Retain = 5
)

$ErrorActionPreference = 'Stop'

function Normalize-Fingerprint([string]$Value) {
    $Value.Trim().ToLowerInvariant().Replace(':', '')
}

if (-not $env:JAVA_HOME) { throw 'JAVA_HOME is required' }
if (-not $env:ANDROID_SDK_ROOT) { throw 'ANDROID_SDK_ROOT is required' }
if (-not (Test-Path -LiteralPath $ApkPath)) { throw "APK not found: $ApkPath" }
if ($Retain -lt 2) { throw 'Retain must be at least 2' }

$projectRoot = Split-Path $PSScriptRoot -Parent
$buildTools = Join-Path $env:ANDROID_SDK_ROOT 'build-tools'
$apksigner = Get-ChildItem -LiteralPath $buildTools -Filter apksigner.bat -File -Recurse |
    Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
$aapt = Get-ChildItem -LiteralPath $buildTools -Filter aapt.exe -File -Recurse |
    Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $apksigner -or -not $aapt) { throw 'Android build tools are incomplete' }

$badging = & $aapt dump badging $ApkPath 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -or $badging -notmatch "package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']+)'") {
    throw 'Unable to read APK package metadata'
}
$packageName = $Matches[1]
$versionCode = [int64]$Matches[2]
$versionName = $Matches[3]
if ($packageName -ne 'io.smsrelay.collector') { throw "Unexpected package name: $packageName" }

$signerOutput = & $apksigner verify --verbose --print-certs $ApkPath 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -or $signerOutput -notmatch 'Signer #1 certificate SHA-256 digest:\s*([0-9a-fA-F]+)') {
    throw 'APK signature verification failed'
}
$signerSha256 = Normalize-Fingerprint $Matches[1]
$hash = (Get-FileHash -LiteralPath $ApkPath -Algorithm SHA256).Hash.ToLowerInvariant()
$size = (Get-Item -LiteralPath $ApkPath).Length

$releaseRoot = Join-Path $projectRoot "web/public/assets/releases/android/$versionCode"
New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
$releaseName = "Relay-SMS-Forwarder-$versionName.apk"
$releasePath = Join-Path $releaseRoot $releaseName
Copy-Item -LiteralPath $ApkPath -Destination $releasePath -Force

$catalogPath = Join-Path $projectRoot 'web/public/assets/releases/release-catalog.json'
$catalog = if (Test-Path -LiteralPath $catalogPath) {
    Get-Content -LiteralPath $catalogPath -Raw -Encoding UTF8 | ConvertFrom-Json
} else {
    [pscustomobject]@{ schema_version = 1; releases = @() }
}

$canonicalDownload = Join-Path $projectRoot 'web/public/assets/downloads/Relay-SMS-Forwarder.apk'
foreach ($existingRelease in @($catalog.releases)) {
    if (
        $existingRelease.platform -ne 'android' -or
        $existingRelease.download_url -ne '/assets/downloads/Relay-SMS-Forwarder.apk'
    ) { continue }
    if (-not (Test-Path -LiteralPath $canonicalDownload)) {
        throw 'The catalog references the canonical Android APK, but that file is missing'
    }
    $existingHash = (Get-FileHash -LiteralPath $canonicalDownload -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($existingHash -ne ([string]$existingRelease.sha256).ToLowerInvariant()) {
        throw 'The canonical Android APK does not match the existing release catalog'
    }
    $existingVersionCode = [int64]$existingRelease.version_code
    $existingVersionName = [string]$existingRelease.version_name
    if ($existingVersionCode -lt 1 -or $existingVersionName -notmatch '^[0-9A-Za-z._-]{1,80}$') {
        throw 'The existing Android release version is invalid'
    }
    $existingDirectory = Join-Path $projectRoot "web/public/assets/releases/android/$existingVersionCode"
    New-Item -ItemType Directory -Force -Path $existingDirectory | Out-Null
    $existingName = "Relay-SMS-Forwarder-$existingVersionName.apk"
    Copy-Item -LiteralPath $canonicalDownload -Destination (Join-Path $existingDirectory $existingName) -Force
    $existingRelease.download_url = "/assets/releases/android/$existingVersionCode/$existingName"
}

$newRelease = [ordered]@{
    platform = 'android'
    channel = $Channel
    version_code = $versionCode
    version_name = $versionName
    published_at = [DateTimeOffset]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
    min_sdk = 26
    mandatory = $Mandatory
    notes = $Notes
    size_bytes = $size
    sha256 = $hash
    signer_sha256 = $signerSha256
    download_url = "/assets/releases/android/$versionCode/$releaseName"
}

$other = @($catalog.releases | Where-Object {
    -not ($_.platform -eq 'android' -and $_.channel -eq $Channel -and [int64]$_.version_code -eq $versionCode)
})
$channelReleases = @($other | Where-Object { $_.platform -eq 'android' -and $_.channel -eq $Channel }) + @([pscustomobject]$newRelease)
$keptChannel = @($channelReleases | Sort-Object { [int64]$_.version_code } -Descending | Select-Object -First $Retain)
$unrelated = @($other | Where-Object { -not ($_.platform -eq 'android' -and $_.channel -eq $Channel) })
$catalog.releases = @($unrelated + $keptChannel)

$temporaryCatalog = "$catalogPath.tmp"
$json = $catalog | ConvertTo-Json -Depth 8
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($temporaryCatalog, $json, $utf8NoBom)
Move-Item -LiteralPath $temporaryCatalog -Destination $catalogPath -Force
Copy-Item -LiteralPath $ApkPath -Destination $canonicalDownload -Force

Write-Output "Published Android $versionName ($versionCode)"
Write-Output "SHA-256: $hash"
Write-Output "Signer SHA-256: $signerSha256"
Write-Output "Catalog: $catalogPath"
