param(
    [string]$Output = "deploy/dist/sms-relay-hub-deploy.zip"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$resolvedOutput = if ([IO.Path]::IsPathRooted($Output)) { $Output } else { Join-Path $repositoryRoot $Output }
$outputDirectory = Split-Path -Parent $resolvedOutput
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
if (Test-Path -LiteralPath $resolvedOutput) { Remove-Item -LiteralPath $resolvedOutput -Force }

$staging = Join-Path ([IO.Path]::GetTempPath()) ("sms-relay-deploy-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $staging | Out-Null
$packagedCatalog = $null
try {
    $publicAssets = Join-Path $repositoryRoot "web/public"
    if (-not (Test-Path -LiteralPath $publicAssets -PathType Container)) { throw "web/public download library is missing" }
    foreach ($relative in @("backend", "clients/script_api", "clients/air724", "clients/ve-tweak", "deploy", "web", "README.md", ".dockerignore")) {
        Copy-Item -LiteralPath (Join-Path $repositoryRoot $relative) -Destination (Join-Path $staging $relative) -Recurse -Force
    }

    $sessionReportSource = Join-Path $repositoryRoot "docs/SESSION_01A06BD1_HANDOFF.md"
    $sessionReportDestination = Join-Path $staging "docs/SESSION_01A06BD1_HANDOFF.md"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $sessionReportDestination) | Out-Null
    Copy-Item -LiteralPath $sessionReportSource -Destination $sessionReportDestination -Force

    Get-ChildItem -LiteralPath (Join-Path $staging "backend") -Directory -Force |
        Where-Object { $_.Name -like ".venv*" } |
        Remove-Item -Recurse -Force

    foreach ($relative in @(
        "web/node_modules",
        "web/dist",
        "web/tsconfig.tsbuildinfo",
        "backend/.venv",
        "backend/.mypy_cache",
        "backend/.pytest_cache",
        "backend/.ruff_cache",
        "deploy/.env",
        "deploy/dist",
        "manager-android"
    )) {
        $path = Join-Path $staging $relative
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    }

    $legacyDownloads = Join-Path $staging "web/public/downloads"
    if (Test-Path -LiteralPath $legacyDownloads) {
        Remove-Item -LiteralPath $legacyDownloads -Recurse -Force
    }

    $releaseRoot = Join-Path $staging "web/public/assets/releases"
    $deployReleaseRoot = Join-Path $releaseRoot "deploy"
    if (Test-Path -LiteralPath $deployReleaseRoot) {
        Remove-Item -LiteralPath $deployReleaseRoot -Recurse -Force
    }

    $catalogPath = Join-Path $releaseRoot "release-catalog.json"
    $catalog = Get-Content -LiteralPath $catalogPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $includedReleases = @(
        $catalog.releases |
            Where-Object { $_.platform -ne "deploy" } |
            Group-Object platform |
            ForEach-Object {
                $_.Group | Sort-Object { [int]$_.version_code } -Descending | Select-Object -First 2
            }
    )
    $catalog.releases = $includedReleases
    # Write UTF-8 with no BOM. Every UTF8 encoding value of the text-writing cmdlets
    # emits a BOM on Windows PowerShell 5.1, and this file ends up in the image at
    # /app/app/static/assets/releases/release-catalog.json (Vite copies web/public into
    # web/dist, the Dockerfile copies web/dist into app/static). The backend parses it
    # with json.loads, which rejects a leading BOM outright, so every
    # /api/v1/releases/... route degrades to 503 release_catalog_invalid and the admin
    # console shows an empty release history. The publish-update.ps1 scripts already
    # write the catalog this way. backend/tests/test_deploy.py asserts the cmdlet form
    # never comes back, so do not name it here either.
    $catalogPath = (Resolve-Path -LiteralPath $catalogPath).ProviderPath
    [System.IO.File]::WriteAllText(
        $catalogPath, ($catalog | ConvertTo-Json -Depth 10), [System.Text.UTF8Encoding]::new($false))
    $packagedCatalog = $catalog

    $publicRoot = Join-Path $staging "web/public"
    $includedUrls = @($includedReleases | ForEach-Object { $_.download_url })
    Get-ChildItem -LiteralPath $releaseRoot -Recurse -File | ForEach-Object {
        $relative = $_.FullName.Substring($publicRoot.Length + 1).Replace("\", "/")
        if ($_.Name -ne "release-catalog.json" -and "/$relative" -notin $includedUrls) {
            Remove-Item -LiteralPath $_.FullName -Force
        }
    }

    Get-ChildItem -LiteralPath (Join-Path $publicRoot "assets/downloads") -File |
        Where-Object { $_.Name -like "sms-relay-root-module-*.zip" } |
        Remove-Item -Force

    Get-ChildItem -LiteralPath $staging -Recurse -Directory -Force |
        Where-Object { $_.Name -in @("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache") } |
        Sort-Object FullName -Descending |
        Remove-Item -Recurse -Force
    Get-ChildItem -LiteralPath $staging -Recurse -File -Force |
        Where-Object { $_.Name -match "\.(db|sqlite|sqlite3)(-(wal|shm))?$" -or $_.Extension -eq ".pyc" } |
        Remove-Item -Force

    # TypeScript incremental build cache. It records absolute paths from whichever
    # machine ran `tsc -b` last (this repo builds the web app inside the image, so a
    # stale file can also make `tsc -b` skip emitting), and it has no business in a
    # shipped package. Sweep by extension so a renamed tsconfig cannot smuggle it in.
    Get-ChildItem -LiteralPath $staging -Recurse -File -Force |
        Where-Object { $_.Name -like "*.tsbuildinfo" } |
        Remove-Item -Force

    # Write entries by hand. The built-in archive cmdlet on Windows PowerShell 5.1
    # stores backslash-separated entry names, which makes Info-ZIP on the server
    # warn ("unzip exit=1") and forces every consumer to normalise paths. Same
    # pattern as clients/root-module/pack.ps1. backend/tests/test_deploy.py asserts
    # this file never calls that cmdlet again, so do not name it here either.
    $stagingRoot = (Resolve-Path -LiteralPath $staging).ProviderPath.TrimEnd('\', '/')
    $zipStream = [System.IO.File]::Open($resolvedOutput, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
    try {
        $zipArchive = New-Object System.IO.Compression.ZipArchive($zipStream, [System.IO.Compression.ZipArchiveMode]::Create)
        try {
            foreach ($file in Get-ChildItem -LiteralPath $stagingRoot -Recurse -File -Force | Sort-Object FullName) {
                $entryName = $file.FullName.Substring($stagingRoot.Length).TrimStart('\', '/').Replace('\', '/')
                [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                    $zipArchive, $file.FullName, $entryName, [System.IO.Compression.CompressionLevel]::Optimal)
            }
        } finally {
            $zipArchive.Dispose()
        }
    } finally {
        $zipStream.Dispose()
    }
}
finally {
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
}

$archive = [IO.Compression.ZipFile]::OpenRead($resolvedOutput)
try {
    # Entry names must use forward slashes: backslash-separated names make
    # Info-ZIP warn on extraction (exit code 1) and would silently defeat every
    # path check below, including the secret and database guards.
    $backslashEntries = @($archive.Entries | Where-Object { $_.FullName.Contains('\') } | ForEach-Object FullName)
    if ($backslashEntries) {
        throw "deployment archive stores backslash-separated entry names: $($backslashEntries -join ', ')"
    }
    $names = @($archive.Entries | ForEach-Object { $_.FullName.Replace('\', '/') })
    $requiredEntries = @(
        "backend/pyproject.toml",
        "deploy/compose.yml",
        "deploy/bootstrap.ps1",
        "deploy/preflight.ps1",
        "deploy/preflight.sh",
        "web/package.json",
        "web/public/assets/downloads/air724-otp-websocket.lua",
        "web/public/assets/downloads/air724-otp-websocket/main.lua",
        "web/public/assets/downloads/air724-otp-websocket/config.example.lua",
        "web/public/assets/downloads/air724-otp-websocket/README.md",
        "web/public/assets/downloads/air724-mqtt-ingest.lua",
        "clients/script_api/otp_client.py",
        "clients/script_api/README.md",
        "clients/air724/main.lua",
        "clients/air724/config.example.lua",
        "clients/air724/README.md",
        "clients/ve-tweak/README.md",
        "clients/ve-tweak/SMSRelayClient.h",
        "clients/ve-tweak/SMSRelayClient.m",
        "clients/ve-tweak/patches/ve-core-2.3.4-sms-relay.patch",
        "backend/app/mqtt_bridge.py",
        "clients/air724/mqtt_ingest.lua",
        "docs/SESSION_01A06BD1_HANDOFF.md",
        ".dockerignore"
    )
    $requiredEntries += "web/public/assets/releases/release-catalog.json"
    foreach ($required in $requiredEntries) {
        if ($names -notcontains $required) { throw "deployment archive entry missing: $required" }
    }
    foreach ($forbidden in @(
        "deploy/.env",
        "deploy/dist",
        "web/node_modules",
        "backend/.venv",
        "backend/.mypy_cache",
        "backend/.pytest_cache",
        "backend/.ruff_cache",
        "manager-android",
        "web/public/downloads"
    )) {
        if ($names | Where-Object { $_ -eq $forbidden -or $_.StartsWith("$forbidden/") }) {
            throw "deployment archive contains local runtime or secret path: $forbidden"
        }
    }
    if ($names | Where-Object { $_ -match "(^|/)(__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache)(/|$)" -or $_ -match "(^|/)\.venv[^/]*(/|$)" -or $_ -match "\.(db|sqlite|sqlite3)(-(wal|shm))?$" -or $_ -match "\.pyc$" }) {
        throw "deployment archive contains local cache or database artifacts"
    }
    $forbiddenArtifactPatterns = @("*.p12", "*.jks", "*.keystore", "*signing.properties")
    if ($names | Where-Object {
        $leafName = Split-Path -Leaf $_
        $_ -match "(^|/)(logs?|screenshots?|temp|tmp)(/|$)" -or
        $_ -match "(^|/)(app-)?debug\.apk$" -or
        $_ -match "-debug\.apk$" -or
        ($forbiddenArtifactPatterns | Where-Object { $leafName -like $_ })
    }) {
        throw "deployment archive contains debug, temporary, or signing artifacts"
    }
    # Non-ASCII entry names decode to invalid UTF-8 on Linux whenever the writer
    # stores them in the OS codepage, which breaks Docker build-context transfer
    # (grpc: string field contains invalid UTF-8). Keep every packaged path ASCII.
    $nonAscii = $names | Where-Object { ($_.ToCharArray() | Where-Object { [int]$_ -gt 127 }).Count -gt 0 }
    if ($nonAscii) {
        throw "deployment archive contains non-ASCII path (breaks docker build): $($nonAscii -join ', ')"
    }
    if ($null -eq $packagedCatalog) { throw "packaged release catalog is missing" }
    # The catalog inside the archive must be BOM-free UTF-8. Python's json.loads
    # refuses a leading BOM, and this file is baked into the image, so a BOM turns
    # every /api/v1/releases/... route into 503 release_catalog_invalid and empties the
    # release history in the admin console and in all three clients.
    $catalogEntryName = "web/public/assets/releases/release-catalog.json"
    $catalogEntry = @($archive.Entries | Where-Object { $_.FullName -eq $catalogEntryName })
    if ($catalogEntry.Count -ne 1) { throw "deployment archive entry missing: $catalogEntryName" }
    $catalogBuffer = New-Object System.IO.MemoryStream
    $catalogStream = $catalogEntry[0].Open()
    try { $catalogStream.CopyTo($catalogBuffer) } finally { $catalogStream.Dispose() }
    $catalogBytes = $catalogBuffer.ToArray()
    $catalogBuffer.Dispose()
    if ($catalogBytes.Length -ge 3 -and $catalogBytes[0] -eq 0xEF -and $catalogBytes[1] -eq 0xBB -and $catalogBytes[2] -eq 0xBF) {
        throw "packaged release catalog starts with a UTF-8 BOM; the backend cannot parse it"
    }
    [void]([System.Text.Encoding]::UTF8.GetString($catalogBytes) | ConvertFrom-Json)
    $catalog = $packagedCatalog
    if (@($catalog.releases | Where-Object { $_.platform -eq "deploy" })) {
        throw "deployment archive must not embed container deploy or update packages"
    }
    foreach ($release in @($catalog.releases)) {
        $releaseEntry = "web/public$($release.download_url)"
        if ($names -notcontains $releaseEntry) {
            throw "deployment archive is missing catalog artifact: $releaseEntry"
        }
    }
    if ($names | Where-Object { $_ -match "(^|/)sms-relay-hub-(deploy|update)(-.*)?\.zip$" }) {
        throw "deployment archive contains an embedded container deployment or update package"
    }
}
finally { $archive.Dispose() }

Get-Item -LiteralPath $resolvedOutput | Select-Object FullName, Length
