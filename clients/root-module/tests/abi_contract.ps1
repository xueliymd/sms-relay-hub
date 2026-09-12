$ErrorActionPreference = "Stop"

$moduleRoot = Split-Path -Parent $PSScriptRoot
# Windows PowerShell 5.1 reads files as the ANSI codepage by default, which mangles the
# Chinese ABI declarations in README.md, so every source read is pinned to UTF-8.
$buildScript = Get-Content -Raw -Encoding UTF8 (Join-Path $moduleRoot "native/build.ps1")
$customizeScript = Get-Content -Raw -Encoding UTF8 (Join-Path $moduleRoot "customize.sh")
$packScript = Get-Content -Raw -Encoding UTF8 (Join-Path $moduleRoot "pack.ps1")
$moduleProp = Get-Content -Raw -Encoding UTF8 (Join-Path $moduleRoot "module.prop")
$readme = Get-Content -Raw -Encoding UTF8 (Join-Path $moduleRoot "README.md")
$contractArchive = Join-Path $moduleRoot "dist/.abi-contract.zip"

# Windows PowerShell 5.1 hosts ship no pwsh, so resolve whichever host is present.
$psHost = $null
foreach ($candidate in @("pwsh", "powershell")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) { $psHost = $candidate; break }
}
if (-not $psHost) { throw "no PowerShell host found: install pwsh or powershell" }

& $psHost -NoProfile -File (Join-Path $moduleRoot "native/build.ps1") -Arch all | Out-Null
if ($LASTEXITCODE -ne 0) { throw "runtime build contract failed" }
& $psHost -NoProfile -File (Join-Path $moduleRoot "pack.ps1") -Output $contractArchive | Out-Null
if ($LASTEXITCODE -ne 0) { throw "default archive build contract failed" }

foreach ($runtime in @("arm64", "arm")) {
    $path = Join-Path $moduleRoot "bin/runtime-linux-$runtime"
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "supported runtime missing: $runtime"
    }
}

foreach ($runtime in @("amd64", "386")) {
    $path = Join-Path $moduleRoot "bin/runtime-linux-$runtime"
    if (Test-Path -LiteralPath $path) {
        throw "unsupported runtime remains: $runtime"
    }
    if ($buildScript.Contains("runtime-linux-$runtime") -or
        $customizeScript.Contains("runtime-linux-$runtime")) {
        throw "unsupported runtime is still referenced: $runtime"
    }
}

if ($customizeScript -match "x86_64|x86") {
    throw "x86 installer branch remains"
}
if ($moduleProp -notmatch '(?m)^version=([0-9]+\.[0-9]+\.[0-9]+)\s*$') {
    throw "module.prop version is missing or not semantic"
}
$expectedVersionName = $Matches[1]
if ($moduleProp -notmatch '(?m)^versionCode=([0-9]+)\s*$') {
    throw "module.prop versionCode is missing or not numeric"
}
$expectedVersionCode = $Matches[1]
if ($readme -notmatch 'arm64-v8a' -or $readme -notmatch 'armeabi-v7a' -or
    $readme -notmatch '不属于当前支持范围') {
    throw "README is missing the supported ABI declarations"
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path $contractArchive))
try {
    $names = @($archive.Entries | ForEach-Object FullName)
    $backslashEntries = @($names | Where-Object { $_.Contains('\') })
    if ($backslashEntries) {
        throw "archive entries use backslash separators, which Magisk cannot unpack: $($backslashEntries -join ', ')"
    }
    $archivedProp = $archive.Entries | Where-Object FullName -eq "module.prop" | Select-Object -First 1
    if (-not $archivedProp) { throw "default archive entry missing: module.prop" }
    $reader = New-Object System.IO.StreamReader($archivedProp.Open())
    try { $archivedPropText = $reader.ReadToEnd() } finally { $reader.Dispose() }
    if ($archivedPropText -notmatch "(?m)^version=$([Regex]::Escape($expectedVersionName))\s*$" -or
        $archivedPropText -notmatch "(?m)^versionCode=$expectedVersionCode\s*$") {
        throw "archived module.prop does not match source $expectedVersionName/$expectedVersionCode"
    }
    foreach ($requiredName in @("bin/runtime-linux-arm64", "bin/runtime-linux-arm", "system/bin/sms-relay-watch")) {
        if ($names -notcontains $requiredName) { throw "default archive entry missing: $requiredName" }
    }
    foreach ($forbiddenName in @("bin/runtime-linux-amd64", "bin/runtime-linux-386")) {
        if ($names -contains $forbiddenName) { throw "default archive entry forbidden: $forbiddenName" }
    }
    $runtimeEntries = @($names | Where-Object { $_ -like "bin/runtime-linux-*" } | Sort-Object)
    $expectedRuntimeEntries = @("bin/runtime-linux-arm", "bin/runtime-linux-arm64")
    if (($runtimeEntries -join '|') -ne ($expectedRuntimeEntries -join '|')) {
        throw "default archive runtime set mismatch: $($runtimeEntries -join ', ')"
    }
} finally {
    $archive.Dispose()
    Remove-Item -LiteralPath $contractArchive -Force -ErrorAction SilentlyContinue
}

Write-Output "PASS root_abi_contract runtimes=arm64,arm version=$expectedVersionName/$expectedVersionCode"
