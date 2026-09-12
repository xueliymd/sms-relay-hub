param(
    [ValidateRange(1, 20)]
    [int]$Keep = 2,
    [string]$BackupDirectory = ".backups/deploy-dist"
)

$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$distDirectory = Join-Path $repositoryRoot "deploy/dist"
$resolvedBackup = if ([IO.Path]::IsPathRooted($BackupDirectory)) {
    $BackupDirectory
} else {
    Join-Path $repositoryRoot $BackupDirectory
}

if (-not (Test-Path -LiteralPath $distDirectory -PathType Container)) {
    throw "deployment package directory is missing: $distDirectory"
}

$patterns = [ordered]@{
    "full" = "^sms-relay-hub-deploy(?:-.*)?\.zip$"
    "update" = "^sms-relay-hub-update(?:-.*)?\.zip$"
}

New-Item -ItemType Directory -Force -Path $resolvedBackup | Out-Null
$moved = 0

foreach ($pattern in $patterns.GetEnumerator()) {
    $packages = Get-ChildItem -LiteralPath $distDirectory -File |
        Where-Object { $_.Name -match $pattern.Value } |
        Sort-Object LastWriteTime -Descending

    foreach ($package in $packages | Select-Object -Skip $Keep) {
        $target = Join-Path $resolvedBackup $package.Name
        if (Test-Path -LiteralPath $target) {
            $target = Join-Path $resolvedBackup (
                "{0}-{1}{2}" -f [IO.Path]::GetFileNameWithoutExtension($package.Name),
                $package.LastWriteTime.ToString("yyyyMMdd-HHmmss"),
                $package.Extension
            )
        }
        Move-Item -LiteralPath $package.FullName -Destination $target
        Write-Host "backed up $($package.Name) -> $target"
        $moved++
    }
}

Write-Host "kept the newest $Keep full packages and $Keep update packages; moved $moved file(s) to $resolvedBackup"
