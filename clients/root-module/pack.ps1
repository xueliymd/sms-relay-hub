param(
    [string]$Output = "dist/sms-relay-root-module.zip"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$moduleRoot = $PSScriptRoot
$required = @(
    "module.prop",
    "service.sh",
    "action.sh",
    "customize.sh",
    "uninstall.sh",
    "system/bin/sms-relay-upload",
    "system/bin/sms-relay-wake",
    "system/bin/sms-relay-watch",
    "bin/runtime-linux-arm64",
    "bin/runtime-linux-arm"
)
foreach ($relative in $required) {
    $path = Join-Path $moduleRoot $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "required module file missing: $relative" }
}

$forbidden = Get-ChildItem -LiteralPath $moduleRoot -Recurse -File | Where-Object Extension -In @(".apk", ".dex")
if ($forbidden) { throw "embedded APK/DEX is forbidden: $($forbidden.FullName -join ', ')" }
$readerReferences = Select-String -Path (Join-Path $moduleRoot "service.sh"), (Join-Path $moduleRoot "config.example.json") -Pattern "reader_command" -SimpleMatch
if ($readerReferences) { throw "external reader_command reference remains" }
$unsupportedRuntimes = Get-ChildItem -LiteralPath (Join-Path $moduleRoot "bin") -File -ErrorAction SilentlyContinue |
    Where-Object Name -In @("runtime-linux-amd64", "runtime-linux-386")
if ($unsupportedRuntimes) { throw "unsupported runtime remains: $($unsupportedRuntimes.Name -join ', ')" }

$resolvedOutput = if ([System.IO.Path]::IsPathRooted($Output)) { $Output } else { Join-Path $moduleRoot $Output }
$outputDirectory = Split-Path -Parent $resolvedOutput
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
if (Test-Path -LiteralPath $resolvedOutput) { Remove-Item -LiteralPath $resolvedOutput -Force }

$staging = Join-Path ([System.IO.Path]::GetTempPath()) ("sms-relay-module-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $staging | Out-Null
try {
    foreach ($relative in @("module.prop", "service.sh", "action.sh", "customize.sh", "uninstall.sh", "config.example.json", "README.md")) {
        Copy-Item -LiteralPath (Join-Path $moduleRoot $relative) -Destination (Join-Path $staging $relative)
    }
    Copy-Item -LiteralPath (Join-Path $moduleRoot "system") -Destination (Join-Path $staging "system") -Recurse
    New-Item -ItemType Directory -Force -Path (Join-Path $staging "bin") | Out-Null
    foreach ($runtime in @("runtime-linux-arm64", "runtime-linux-arm")) {
        Copy-Item -LiteralPath (Join-Path $moduleRoot "bin/$runtime") -Destination (Join-Path $staging "bin/$runtime")
    }
    # Write entries by hand: Compress-Archive on Windows PowerShell 5.1 stores backslash
    # separators, which Magisk/KernelSU/APatch cannot unpack into real directories.
    $stagingRoot = (Resolve-Path -LiteralPath $staging).ProviderPath.TrimEnd('\', '/')
    $zipStream = [System.IO.File]::Open($resolvedOutput, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
    try {
        $zipArchive = New-Object System.IO.Compression.ZipArchive($zipStream, [System.IO.Compression.ZipArchiveMode]::Create)
        try {
            foreach ($file in Get-ChildItem -LiteralPath $stagingRoot -Recurse -File | Sort-Object FullName) {
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
} finally {
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
}

$archive = [System.IO.Compression.ZipFile]::OpenRead($resolvedOutput)
try {
    $names = @($archive.Entries | ForEach-Object FullName)
    foreach ($relative in $required) {
        $zipName = $relative.Replace('\', '/')
        if ($names -notcontains $zipName) { throw "archive entry missing: $zipName" }
    }
    foreach ($forbiddenName in @("bin/runtime-linux-amd64", "bin/runtime-linux-386")) {
        if ($names -contains $forbiddenName) { throw "archive entry forbidden: $forbiddenName" }
    }
} finally {
    $archive.Dispose()
}

Get-Item -LiteralPath $resolvedOutput | Select-Object FullName, Length
