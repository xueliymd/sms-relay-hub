param(
    [ValidateSet("all", "arm64", "arm")]
    [string]$Arch = "all"
)

$ErrorActionPreference = "Stop"
$moduleRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $PSScriptRoot "runtime"
$binRoot = Join-Path $moduleRoot "bin"
New-Item -ItemType Directory -Force -Path $binRoot | Out-Null

$architectures = if ($Arch -eq "all") { @("arm64", "arm") } else { @($Arch) }
$staleRuntimes = Get-ChildItem -LiteralPath $binRoot -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "runtime-linux-*" -and $_.Name -notin @("runtime-linux-arm64", "runtime-linux-arm") }
foreach ($staleRuntime in $staleRuntimes) {
    Remove-Item -LiteralPath $staleRuntime.FullName -Force
}
$savedEnvironment = @{
    CGO_ENABLED = $env:CGO_ENABLED
    GOOS = $env:GOOS
    GOARCH = $env:GOARCH
    GOARM = $env:GOARM
}

try {
    $env:CGO_ENABLED = "0"
    $env:GOOS = "linux"
    Push-Location $runtimeRoot
    try {
        foreach ($architecture in $architectures) {
            $env:GOARCH = $architecture
            if ($architecture -eq "arm") { $env:GOARM = "7" } else { Remove-Item Env:GOARM -ErrorAction SilentlyContinue }
            $output = Join-Path $binRoot "runtime-linux-$architecture"
            $arguments = @("build")
            $arguments += @("-tags", "sqlite_fallback")
            $arguments += @("-trimpath", "-buildvcs=false", "-ldflags", "-s -w", "-o", $output, ".")
            & go @arguments
            if ($LASTEXITCODE -ne 0) { throw "runtime build failed for $architecture" }
            if (-not (Test-Path -LiteralPath $output -PathType Leaf)) { throw "runtime output missing for $architecture" }
        }
    } finally {
        Pop-Location
    }
} finally {
    foreach ($name in $savedEnvironment.Keys) {
        $value = $savedEnvironment[$name]
        if ($null -eq $value) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue } else { Set-Item "Env:$name" $value }
    }
}

Get-ChildItem -LiteralPath $binRoot -File | Where-Object Name -Like "runtime-linux-*" | Select-Object Name, Length
