[CmdletBinding()]
param(
    [string]$EnvFile = (Join-Path $PSScriptRoot '.env'),
    [switch]$SkipDocker,
    [switch]$SkipDns
)

$ErrorActionPreference = 'Stop'
$composeFile = Join-Path $PSScriptRoot 'compose.yml'
$errors = [System.Collections.Generic.List[string]]::new()
$warnings = [System.Collections.Generic.List[string]]::new()

function Read-EnvFile {
    param([string]$Path)
    $values = @{}
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $values }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $values[$Matches[1]] = $Matches[2].Trim()
        }
    }
    return $values
}

function Add-CheckError { param([string]$Message) $errors.Add($Message) }
function Add-CheckWarning { param([string]$Message) $warnings.Add($Message) }

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    Add-CheckError "env file not found: $EnvFile"
    $values = @{}
} else {
    $values = Read-EnvFile -Path $EnvFile
}

foreach ($name in @('ADMIN_USERNAME', 'ADMIN_PASSWORD', 'ADMIN_TOKEN', 'DATA_ENCRYPTION_KEY', 'POSTGRES_PASSWORD', 'DATABASE_URL', 'RELAY_SITE', 'HTTP_PORT', 'HTTPS_PORT', 'APP_PORT')) {
    if (-not $values.ContainsKey($name) -or [string]::IsNullOrWhiteSpace($values[$name])) {
        Add-CheckError "$name is missing or empty"
    }
}

function Get-PortValue {
    param([string]$Name)
    if (-not $values.ContainsKey($Name)) { return $null }
    $number = 0
    if (-not [int]::TryParse($values[$Name], [ref]$number) -or $number -lt 1 -or $number -gt 65535) {
        Add-CheckError "$Name must be an integer from 1 to 65535"
        return $null
    }
    return $number
}

$httpPort = Get-PortValue -Name 'HTTP_PORT'
$httpsPort = Get-PortValue -Name 'HTTPS_PORT'
$appPort = Get-PortValue -Name 'APP_PORT'
if ($null -ne $httpPort -and $null -ne $httpsPort -and $httpPort -eq $httpsPort) {
    Add-CheckError 'HTTP_PORT and HTTPS_PORT must be different'
}
if ($null -ne $appPort -and (($appPort -eq $httpPort) -or ($appPort -eq $httpsPort))) {
    Add-CheckWarning 'APP_PORT is an internal container port; it matches a host port. This is valid, but a different value is easier to diagnose.'
}

$site = if ($values.ContainsKey('RELAY_SITE')) { $values.RELAY_SITE.Trim() } else { '' }
$relayDomain = if ($values.ContainsKey('RELAY_DOMAIN')) { $values.RELAY_DOMAIN.Trim() } else { '' }
$siteHost = $null
if ($site -match '^:\d+$') {
    $sitePort = 0
    if (-not [int]::TryParse($site.Substring(1), [ref]$sitePort) -or $sitePort -lt 1 -or $sitePort -gt 65535) {
        Add-CheckError 'RELAY_SITE listen port is invalid'
    }
    if (-not $relayDomain) {
        Add-CheckWarning 'RELAY_DOMAIN is empty; only the HTTP/IP entry is enabled.'
    }
} elseif ($site -match '^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$' -or $site -match '^\[[0-9A-Fa-f:]+\]$') {
    if (-not $relayDomain) {
        Add-CheckWarning 'RELAY_SITE contains a domain for backward compatibility; prefer RELAY_SITE=:80 plus RELAY_DOMAIN.'
    }
} elseif ($site) {
    Add-CheckError 'RELAY_SITE must be :PORT or a DNS hostname without scheme/path'
}

if ($relayDomain) {
    if ($relayDomain -notmatch '^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$') {
        Add-CheckError 'RELAY_DOMAIN must be a DNS hostname without scheme, path, or port'
    } else {
        $siteHost = $relayDomain
    }
    if ($site -ne ':80') {
        Add-CheckError 'RELAY_SITE must stay :80 in dual mode; put the DNS name in RELAY_DOMAIN'
    }
    if ($null -ne $httpsPort -and $httpsPort -ne 443) {
        Add-CheckWarning "HTTPS_PORT is $httpsPort; the domain URL must include this port unless it is 443."
    }
}

$publicBaseUrl = if ($values.ContainsKey('PUBLIC_BASE_URL')) { $values.PUBLIC_BASE_URL.Trim() } else { '' }
if (-not $publicBaseUrl) {
    Add-CheckWarning 'PUBLIC_BASE_URL is empty; clients must use the manually configured server address.'
} else {
    try {
        $uri = [Uri]::new($publicBaseUrl)
        if ($uri.Scheme -notin @('http', 'https') -or -not $uri.Host -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or ($uri.AbsolutePath -notin @('', '/'))) {
            throw 'invalid URL shape'
        }
        if ($uri.Scheme -eq 'http') { Add-CheckWarning 'PUBLIC_BASE_URL uses HTTP; use HTTPS for public iPhone and admin traffic.' }
        if ($uri.Scheme -eq 'https' -and -not $relayDomain) {
            Add-CheckWarning 'PUBLIC_BASE_URL is HTTPS but RELAY_DOMAIN is empty, so Caddy only serves the HTTP/IP entry; set RELAY_DOMAIN or point PUBLIC_BASE_URL at the address clients can actually reach.'
        }
        if ($siteHost -and $uri.Host -ne $siteHost) { Add-CheckError 'PUBLIC_BASE_URL host does not match RELAY_DOMAIN' }
    } catch {
        Add-CheckError 'PUBLIC_BASE_URL must be an HTTP(S) origin without credentials, path, query, or fragment'
    }
}

if ($siteHost -and -not $SkipDns -and (Get-Command Resolve-DnsName -ErrorAction SilentlyContinue)) {
    try {
        $dns = @(Resolve-DnsName -Name $siteHost -Type A -ErrorAction Stop)
        if (-not $dns) { throw 'no A record' }
    } catch {
        try {
            $dns = @(Resolve-DnsName -Name $siteHost -Type AAAA -ErrorAction Stop)
            if (-not $dns) { throw 'no AAAA record' }
        } catch {
            Add-CheckWarning "DNS did not resolve $siteHost from this machine"
        }
    }
}

if (-not $SkipDocker) {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) {
        Add-CheckWarning 'docker command is not available; skipped docker compose config validation'
    } else {
        & $docker.Source compose --env-file $EnvFile -f $composeFile config --quiet 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Add-CheckError 'docker compose config validation failed' }

        # Single-instance guard, mirroring deploy/preflight.sh and
        # deploy/apply-update.sh. Two Compose projects on one compose file mean two
        # databases, and the entry point then serves an empty stack while the real
        # data stays in the other volume. docker ps renders .Labels as a single
        # comma-joined string, so `index .Labels "key"` is a template error that
        # silently hides every project; `.Label "key"` is the per-label accessor.
        # A Windows workspace check normally sees Linux label paths that cannot
        # match $PSScriptRoot, so this reports nothing instead of false positives;
        # the server-side check is deploy/preflight.sh.
        $labelFormat = '{{.Label "com.docker.compose.project"}}|{{.Label "com.docker.compose.service"}}|{{.Label "com.docker.compose.project.config_files"}}|{{.Label "com.docker.compose.project.working_dir"}}'
        $composeRows = @(& $docker.Source ps -a --filter 'label=com.docker.compose.project' --format $labelFormat)
        if ($LASTEXITCODE -ne 0) {
            Add-CheckWarning 'could not read Compose container labels; skipped the duplicate stack check'
        } else {
            $deployDir = (Resolve-Path -LiteralPath $PSScriptRoot).Path
            $deployComposeFile = Join-Path $deployDir 'compose.yml'
            $installProjects = @(
                $composeRows | ForEach-Object {
                    $parts = $_ -split '\|', 4
                    if ($parts.Count -eq 4 -and $parts[0] -and ($parts[1] -in @('app', 'caddy'))) {
                        if ($parts[2].Contains($deployComposeFile) -or $parts[3] -eq $deployDir) { $parts[0] }
                    }
                } | Sort-Object -Unique
            )
            $envProject = if ($values.ContainsKey('COMPOSE_PROJECT_NAME')) { $values.COMPOSE_PROJECT_NAME.Trim() } else { '' }
            if ($installProjects.Count -gt 1) {
                Add-CheckError "multiple Compose projects use ${deployComposeFile}: $($installProjects -join ' '); keep the project that holds real data and remove only the verified empty project"
            } elseif ($installProjects.Count -eq 1) {
                Write-Output "Compose project in use: $($installProjects[0])"
                if ($envProject -and $envProject -ne $installProjects[0]) {
                    Add-CheckError "COMPOSE_PROJECT_NAME=$envProject does not match the running Compose project $($installProjects[0]); updating under the wrong project name creates a second empty database"
                }
            } elseif ($envProject) {
                Add-CheckWarning "COMPOSE_PROJECT_NAME=$envProject is set by hand; leave it empty so Compose keeps the existing project name"
            }

            $postgresVolumes = @(& $docker.Source volume ls --format '{{.Name}}' | Where-Object { $_ -match '_postgres_data$' } | Sort-Object -Unique)
            if ($postgresVolumes.Count -gt 1) {
                Add-CheckWarning "more than one database volume exists: $($postgresVolumes -join ' '); confirm which volume holds real data before updating"
            }
        }
    }
}

if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
    foreach ($port in @($httpPort, $httpsPort)) {
        if ($null -ne $port) {
            $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
            if ($listeners) { Add-CheckWarning "host port $port is already listening; verify it belongs to this Compose instance" }
        }
    }
}

foreach ($warning in $warnings) { Write-Warning $warning }
if ($errors.Count) {
    foreach ($errorMessage in $errors) { Write-Error $errorMessage }
    exit 1
}
Write-Output "Preflight passed: $EnvFile"
Write-Output "Ports: HTTP=$httpPort HTTPS=$httpsPort APP=$appPort"
if ($siteHost) { Write-Output "HTTPS hostname: $siteHost" }
exit 0
