param(
    [Parameter(Mandatory = $true)][string]$ArchivePath,
    [Parameter(Mandatory = $true)][string]$Notes,
    [int64]$VersionCode = 20260901,
    [string]$VersionName = '2026.09.01',
    [string]$Channel = 'stable',
    [int]$Retain = 5
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $ArchivePath -PathType Leaf)) { throw "Container update archive not found: $ArchivePath" }
if ($VersionCode -lt 1 -or $VersionName -notmatch '^[0-9A-Za-z._-]{1,80}$') { throw 'Container update version is invalid' }
if ($Retain -lt 2) { throw 'Retain must be at least 2' }

$projectRoot = Split-Path $PSScriptRoot -Parent
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead((Resolve-Path $ArchivePath))
try {
    $names = @($archive.Entries | ForEach-Object { $_.FullName.Replace('\', '/') })
    foreach ($required in @('backend/pyproject.toml', 'deploy/compose.yml', 'deploy/apply-update.sh', 'web/package.json')) {
        if ($names -notcontains $required) { throw "Container update archive entry missing: $required" }
    }
    $catalogEntry = $archive.Entries |
        Where-Object { $_.FullName.Replace('\', '/') -eq 'web/public/assets/releases/release-catalog.json' } |
        Select-Object -First 1
    if ($null -eq $catalogEntry) { throw 'Container update archive is missing release-catalog.json' }
    $reader = New-Object System.IO.StreamReader($catalogEntry.Open(), [System.Text.Encoding]::UTF8)
    try {
        $packagedCatalog = $reader.ReadToEnd() | ConvertFrom-Json
    }
    finally {
        $reader.Dispose()
    }
    if (@($packagedCatalog.releases | Where-Object { $_.platform -eq 'deploy' })) {
        throw 'Container update archive must not embed container deployment or update packages'
    }
    if ($names | Where-Object { $_ -match '(^|/)sms-relay-hub-(deploy|update)(-.*)?\.zip$' }) {
        throw 'Container update archive must not embed container deployment or update packages'
    }
} finally {
    $archive.Dispose()
}

$hash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
$size = (Get-Item -LiteralPath $ArchivePath).Length
$releaseRoot = Join-Path $projectRoot "web/public/assets/releases/deploy/$VersionCode"
New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
$releaseName = "sms-relay-hub-update-$VersionName.zip"
Copy-Item -LiteralPath $ArchivePath -Destination (Join-Path $releaseRoot $releaseName) -Force

$catalogPath = Join-Path $projectRoot 'web/public/assets/releases/release-catalog.json'
$catalog = Get-Content -LiteralPath $catalogPath -Raw -Encoding UTF8 | ConvertFrom-Json
$newRelease = [ordered]@{
    platform = 'deploy'
    channel = $Channel
    version_code = $VersionCode
    version_name = $VersionName
    published_at = [DateTimeOffset]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
    min_sdk = 21
    mandatory = $false
    notes = $Notes
    size_bytes = $size
    sha256 = $hash
    signer_sha256 = $null
    download_url = "/assets/releases/deploy/$VersionCode/$releaseName"
}

$other = @($catalog.releases | Where-Object {
    -not ($_.platform -eq 'deploy' -and $_.channel -eq $Channel -and [int64]$_.version_code -eq $VersionCode)
})
$channelReleases = @($other | Where-Object { $_.platform -eq 'deploy' -and $_.channel -eq $Channel }) + @([pscustomobject]$newRelease)
$keptChannel = @($channelReleases | Sort-Object { [int64]$_.version_code } -Descending | Select-Object -First $Retain)
$unrelated = @($other | Where-Object { -not ($_.platform -eq 'deploy' -and $_.channel -eq $Channel) })
$catalog.releases = @($unrelated + $keptChannel)

$temporaryCatalog = "$catalogPath.tmp"
$json = $catalog | ConvertTo-Json -Depth 8
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($temporaryCatalog, $json, $utf8NoBom)
Move-Item -LiteralPath $temporaryCatalog -Destination $catalogPath -Force

Write-Output "Published container update $VersionName ($VersionCode)"
Write-Output "SHA-256: $hash"
Write-Output "Catalog: $catalogPath"
