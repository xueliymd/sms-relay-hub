param(
    [Parameter(Mandatory = $true)][string]$ArchivePath,
    [Parameter(Mandatory = $true)][string]$Notes,
    [string]$Channel = 'stable',
    [int]$Retain = 5
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $ArchivePath -PathType Leaf)) { throw "Root module archive not found: $ArchivePath" }
if ($Retain -lt 2) { throw 'Retain must be at least 2' }

$moduleRoot = $PSScriptRoot
$projectRoot = Split-Path (Split-Path $moduleRoot -Parent) -Parent
$modulePropPath = Join-Path $moduleRoot 'module.prop'
$moduleProp = Get-Content -LiteralPath $modulePropPath -Raw
if ($moduleProp -notmatch '(?m)^version=([^\r\n]+)$') { throw 'Root module version is missing' }
$versionName = $Matches[1].Trim()
if ($moduleProp -notmatch '(?m)^versionCode=([0-9]+)$') { throw 'Root module versionCode is missing' }
$versionCode = [int64]$Matches[1]

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead((Resolve-Path $ArchivePath))
try {
    $modulePropEntry = $archive.Entries | Where-Object FullName -eq 'module.prop' | Select-Object -First 1
    if (-not $modulePropEntry) { throw 'Root module archive does not contain module.prop' }
    $reader = New-Object IO.StreamReader($modulePropEntry.Open())
    try { $archivedModuleProp = $reader.ReadToEnd() } finally { $reader.Dispose() }
    if ($archivedModuleProp -notmatch "(?m)^version=$([Regex]::Escape($versionName))\s*$" -or
        $archivedModuleProp -notmatch "(?m)^versionCode=$versionCode\s*$") {
        throw 'Root module archive version does not match source module.prop'
    }
} finally {
    $archive.Dispose()
}

$hash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
$size = (Get-Item -LiteralPath $ArchivePath).Length
$releaseRoot = Join-Path $projectRoot "web/public/assets/releases/root/$versionCode"
New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
$releaseName = "sms-relay-root-module-$versionName.zip"
Copy-Item -LiteralPath $ArchivePath -Destination (Join-Path $releaseRoot $releaseName) -Force

$catalogPath = Join-Path $projectRoot 'web/public/assets/releases/release-catalog.json'
$catalog = Get-Content -LiteralPath $catalogPath -Raw -Encoding UTF8 | ConvertFrom-Json
$newRelease = [ordered]@{
    platform = 'root'
    channel = $Channel
    version_code = $versionCode
    version_name = $versionName
    published_at = [DateTimeOffset]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
    min_sdk = 21
    mandatory = $false
    notes = $Notes
    size_bytes = $size
    sha256 = $hash
    signer_sha256 = $null
    download_url = "/assets/releases/root/$versionCode/$releaseName"
}

$other = @($catalog.releases | Where-Object {
    -not ($_.platform -eq 'root' -and $_.channel -eq $Channel -and [int64]$_.version_code -eq $versionCode)
})
$channelReleases = @($other | Where-Object { $_.platform -eq 'root' -and $_.channel -eq $Channel }) + @([pscustomobject]$newRelease)
$keptChannel = @($channelReleases | Sort-Object { [int64]$_.version_code } -Descending | Select-Object -First $Retain)
$unrelated = @($other | Where-Object { -not ($_.platform -eq 'root' -and $_.channel -eq $Channel) })
$catalog.releases = @($unrelated + $keptChannel)

$temporaryCatalog = "$catalogPath.tmp"
$json = $catalog | ConvertTo-Json -Depth 8
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($temporaryCatalog, $json, $utf8NoBom)
Move-Item -LiteralPath $temporaryCatalog -Destination $catalogPath -Force

Write-Output "Published Root module $versionName ($versionCode)"
Write-Output "SHA-256: $hash"
Write-Output "Catalog: $catalogPath"
