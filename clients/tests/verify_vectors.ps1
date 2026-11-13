[CmdletBinding()]
param(
    [string]$RepositoryRoot,
    [string]$VectorPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
}
if ([string]::IsNullOrWhiteSpace($VectorPath)) {
    $VectorPath = Join-Path $PSScriptRoot 'signature_vectors.json'
}

function ConvertFrom-Base64Url {
    param([Parameter(Mandatory)][string]$Value)

    if ($Value -notmatch '^[A-Za-z0-9_-]+$') {
        throw "Invalid base64url value"
    }
    $padded = $Value.Replace('-', '+').Replace('_', '/')
    $padded += '=' * ((4 - ($padded.Length % 4)) % 4)
    return [Convert]::FromBase64String($padded)
}

function ConvertTo-Base64Url {
    param([Parameter(Mandatory)][byte[]]$Bytes)

    return ([Convert]::ToBase64String($Bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_'))
}

function Assert-NotPlaceholderSecret {
    param([Parameter(Mandatory)][string]$Secret)

    if ([string]::IsNullOrWhiteSpace($Secret)) {
        throw "Secret must not be empty"
    }
    $decoded = ConvertFrom-Base64Url $Secret
    if ($decoded.Length -ne 32) {
        throw "HMAC test secret must decode to exactly 32 bytes"
    }

    $allZero = $true
    $allSame = $true
    $firstByte = $decoded[0]
    foreach ($byte in $decoded) {
        if ($byte -ne 0) {
            $allZero = $false
        }
        if ($byte -ne $firstByte) {
            $allSame = $false
        }
    }
    if ($allZero) {
        throw "All-zero HMAC test secret is not allowed"
    }
    if ($allSame) {
        throw "Single-byte repeated HMAC test secret is not allowed"
    }

    try {
        $decodedText = [Text.UTF8Encoding]::new($false, $true).GetString($decoded)
        $decodedLower = $decodedText.ToLowerInvariant()
        foreach ($marker in @('placeholder', 'change_me', 'changeme', 'replace_me', 'replace-me', 'your_secret', 'your-secret', 'todo', 'xxx')) {
            if ($decodedLower.Contains($marker)) {
                throw "Placeholder secret is not allowed"
            }
        }
    } catch [Text.DecoderFallbackException] {
        # Arbitrary binary test keys are valid; only inspect decoded text when
        # the full key is valid UTF-8.
    }
}

function Get-Sha256Hex {
    param([Parameter(Mandatory)][byte[]]$Bytes)

    $digest = [Security.Cryptography.SHA256]::Create().ComputeHash($Bytes)
    return ([BitConverter]::ToString($digest) -replace '-', '').ToLowerInvariant()
}

function Assert-Equal {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string]$Actual,
        [Parameter(Mandatory)][string]$Expected
    )

    if ($Actual -cne $Expected) {
        throw "$Label mismatch: expected '$Expected', got '$Actual'"
    }
}

function Assert-Contains {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string]$Text,
        [Parameter(Mandatory)][string]$Pattern
    )

    if ($Text -notmatch $Pattern) {
        throw "$Label is missing required pattern: $Pattern"
    }
}

function Assert-NotContains {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string]$Text,
        [Parameter(Mandatory)][string]$Pattern
    )

    if ($Text -match $Pattern) {
        throw "$Label contains forbidden pattern: $Pattern"
    }
}

function Assert-AsciiUtf8LfFile {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label absent: $Path"
    }
    $bytes = [IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -eq 0) {
        throw "$Label must not be empty"
    }
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        throw "$Label must be UTF-8 without a BOM"
    }
    if ([Array]::IndexOf($bytes, [byte]13) -ge 0) {
        throw "$Label must use LF line endings"
    }
    if ($bytes[$bytes.Length - 1] -ne 10) {
        throw "$Label must end with LF"
    }
    foreach ($byte in $bytes) {
        if ($byte -gt 0x7F) {
            throw "$Label must contain ASCII text only"
        }
    }
    try {
        [Text.UTF8Encoding]::new($false, $true).GetString($bytes) | Out-Null
    } catch {
        throw "$Label must contain valid UTF-8"
    }
}

function Test-StrictFlatConfigText {
    param([Parameter(Mandatory)][string]$Text)

    $normalized = $Text -replace "`r", ''
    $lines = $normalized -split "`n"
    if ($lines.Count -gt 0 -and $lines[$lines.Count - 1] -eq '') {
        $lines = $lines[0..($lines.Count - 2)]
    }
    if ($lines.Count -lt 3 -or $lines[0].Trim() -cne '{' -or $lines[$lines.Count - 1].Trim() -cne '}') {
        return $false
    }
    $seen = @{}
    for ($i = 1; $i -lt ($lines.Count - 1); $i++) {
        $line = $lines[$i].Trim()
        if ([string]::IsNullOrWhiteSpace($line)) { return $false }
        $hasComma = $line.EndsWith(',')
        $token = if ($hasComma) { $line.Substring(0, $line.Length - 1).TrimEnd() } else { $line }
        $isLast = $i -eq ($lines.Count - 2)
        if ($isLast -and $hasComma) { return $false }
        if (-not $isLast -and -not $hasComma) { return $false }

        $match = [regex]::Match($token, '^"([A-Za-z_][A-Za-z0-9_]*)"\s*:\s*"([^"\\]*)"$')
        $kind = 'string'
        if (-not $match.Success) {
            $match = [regex]::Match($token, '^"([A-Za-z_][A-Za-z0-9_]*)"\s*:\s*(true|false)$')
            $kind = 'boolean'
        }
        if (-not $match.Success) {
            $match = [regex]::Match($token, '^"([A-Za-z_][A-Za-z0-9_]*)"\s*:\s*(0|[1-9][0-9]*)$')
            $kind = 'integer'
        }
        if (-not $match.Success) { return $false }
        $key = $match.Groups[1].Value
        if ($seen.ContainsKey($key)) { return $false }
        $seen[$key] = $kind
    }
    return $true
}

function Get-HarnessJsonDepth {
    param([Parameter(Mandatory)][string]$Json)

    $depth = 0
    $maximum = 0
    $inString = $false
    $escaped = $false
    foreach ($character in $Json.ToCharArray()) {
        if ($inString) {
            if ($escaped) {
                $escaped = $false
            } elseif ($character -eq '\') {
                $escaped = $true
            } elseif ($character -eq '"') {
                $inString = $false
            }
            continue
        }
        if ($character -eq '"') {
            $inString = $true
        } elseif ($character -eq '{' -or $character -eq '[') {
            $depth++
            if ($depth -gt $maximum) { $maximum = $depth }
        } elseif ($character -eq '}' -or $character -eq ']') {
            $depth--
            if ($depth -lt 0) { throw 'Malformed JSON structure' }
        }
    }
    if ($inString -or $escaped -or $depth -ne 0) { throw 'Malformed JSON structure' }
    return $maximum
}

function Get-HarnessLocalBodyOutcome {
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][byte[]]$RawBytes,
        [Parameter(Mandatory)][AllowEmptyString()][string]$Json
    )

    if ($RawBytes.Length -eq 0) { return 'local_body_invalid' }
    if ($RawBytes.Length -gt 16384) { return 'local_body_too_large' }
    try {
        $Json | ConvertFrom-Json -ErrorAction Stop | Out-Null
        if ((Get-HarnessJsonDepth $Json) -gt 32) { return 'local_body_invalid' }
    } catch {
        return 'local_body_invalid'
    }
    return 'body_ready'
}

function Get-HarnessServiceAction {
    param([Parameter(Mandatory)][string]$Outcome)

    if ($Outcome -match '^2[0-9][0-9]$') { return 'success' }
    if ($Outcome -in @('409', '422', 'local_body_invalid', 'local_body_too_large', 'local_body_read_failed')) {
        return 'quarantine'
    }
    return 'retry'
}

function New-HarnessUniqueQuarantinePath {
    param(
        [Parameter(Mandatory)][string]$Directory,
        [Parameter(Mandatory)][string]$BaseName
    )

    for ($suffix = 0; $suffix -le 1000; $suffix++) {
        $candidateName = if ($suffix -eq 0) { $BaseName } else { "$BaseName.$suffix" }
        $candidate = Join-Path $Directory $candidateName
        try {
            $stream = [IO.File]::Open($candidate, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $stream.Dispose()
            return $candidate
        } catch [IO.IOException] {
            continue
        }
    }
    throw 'Unable to reserve unique quarantine path'
}

function Invoke-HarnessQuarantineTransfer {
    param(
        [Parameter(Mandatory)][string]$SpoolPath,
        [Parameter(Mandatory)][string]$QuarantineDirectory,
        [switch]$SimulateLinkFailure,
        [switch]$SimulateDeleteFailure
    )

    if ($SimulateLinkFailure) {
        return 'quarantine_failed'
    }
    $target = New-HarnessUniqueQuarantinePath $QuarantineDirectory ([IO.Path]::GetFileName($SpoolPath))
    [IO.File]::WriteAllBytes($target, [IO.File]::ReadAllBytes($SpoolPath))
    if ($SimulateDeleteFailure) {
        [IO.File]::Delete($target)
        return 'quarantine_failed'
    }
    [IO.File]::Delete($SpoolPath)
    return [IO.Path]::GetFileName($target)
}

function Invoke-RootContractHarness {
    $utf8 = [Text.UTF8Encoding]::new($false)
    $baseJson = '{"source":"root_module","client_message_id":"id"}'
    $body16384 = $baseJson + (' ' * (16384 - $utf8.GetByteCount($baseJson)))
    $body16385 = $body16384 + ' '
    Assert-Equal -Label 'Root harness 16384 bytes' -Actual (Get-HarnessLocalBodyOutcome $utf8.GetBytes($body16384) $body16384) -Expected 'body_ready'
    Assert-Equal -Label 'Root harness 16385 bytes' -Actual (Get-HarnessLocalBodyOutcome $utf8.GetBytes($body16385) $body16385) -Expected 'local_body_too_large'

    $depth32 = ('{"v":' * 32) + '0' + ('}' * 32)
    $depth33 = ('{"v":' * 33) + '0' + ('}' * 33)
    Assert-Equal -Label 'Root harness depth 32' -Actual (Get-HarnessLocalBodyOutcome $utf8.GetBytes($depth32) $depth32) -Expected 'body_ready'
    Assert-Equal -Label 'Root harness depth 33' -Actual (Get-HarnessLocalBodyOutcome $utf8.GetBytes($depth33) $depth33) -Expected 'local_body_invalid'
    Assert-Equal -Label 'Root harness malformed JSON' -Actual (Get-HarnessLocalBodyOutcome $utf8.GetBytes('{bad') '{bad') -Expected 'local_body_invalid'
    Assert-Equal -Label 'Root harness empty body' -Actual (Get-HarnessLocalBodyOutcome ([byte[]]@()) '') -Expected 'local_body_invalid'

    $outcomeMatrix = @{
        '200' = 'success'; '201' = 'success'; '409' = 'quarantine'; '422' = 'quarantine'
        'local_body_invalid' = 'quarantine'; 'local_body_too_large' = 'quarantine'; 'local_body_read_failed' = 'quarantine'
        '400' = 'retry'; '404' = 'retry'; '401' = 'retry'; '403' = 'retry'; '429' = 'retry'; '500' = 'retry'; '503' = 'retry'; 'transport_error' = 'retry'
    }
    foreach ($outcome in $outcomeMatrix.Keys) {
        Assert-Equal -Label "Root harness outcome $outcome" -Actual (Get-HarnessServiceAction $outcome) -Expected $outcomeMatrix[$outcome]
    }

    $harnessDirectory = Join-Path ([IO.Path]::GetTempPath()) ("sms-relay-root-harness-" + [Guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($harnessDirectory) | Out-Null
    try {
        $first = New-HarnessUniqueQuarantinePath $harnessDirectory 'message.json'
        [IO.File]::WriteAllText($first, 'first')
        $second = New-HarnessUniqueQuarantinePath $harnessDirectory 'message.json'
        Assert-Equal -Label 'Root harness first quarantine name' -Actual ([IO.Path]::GetFileName($first)) -Expected 'message.json'
        Assert-Equal -Label 'Root harness duplicate quarantine name' -Actual ([IO.Path]::GetFileName($second)) -Expected 'message.json.1'
        Assert-Equal -Label 'Root harness original quarantine preserved' -Actual ([IO.File]::ReadAllText($first)) -Expected 'first'

        $linkFailureSource = Join-Path $harnessDirectory 'link-failure-source.json'
        [IO.File]::WriteAllText($linkFailureSource, 'link-failure')
        Assert-Equal -Label 'Root harness link failure outcome' -Actual (Invoke-HarnessQuarantineTransfer $linkFailureSource $harnessDirectory -SimulateLinkFailure) -Expected 'quarantine_failed'
        Assert-Equal -Label 'Root harness link failure preserves spool' -Actual ([IO.File]::Exists($linkFailureSource).ToString()) -Expected 'True'

        $deleteFailureSource = Join-Path $harnessDirectory 'delete-failure-source.json'
        [IO.File]::WriteAllText($deleteFailureSource, 'delete-failure')
        Assert-Equal -Label 'Root harness delete failure outcome' -Actual (Invoke-HarnessQuarantineTransfer $deleteFailureSource $harnessDirectory -SimulateDeleteFailure) -Expected 'quarantine_failed'
        Assert-Equal -Label 'Root harness delete failure preserves spool' -Actual ([IO.File]::Exists($deleteFailureSource).ToString()) -Expected 'True'
    } finally {
        [IO.Directory]::Delete($harnessDirectory, $true)
    }
    Write-Output 'PASS root_contract_harness cases=28 environment=powershell_stub'
}

$document = Get-Content -Raw -Encoding UTF8 -LiteralPath $VectorPath | ConvertFrom-Json
if ($document.version -ne 1) {
    throw "Unsupported signature vector version: $($document.version)"
}
if ([string]$document.canonicalization -cne 'timestamp\nnonce\nsha256(raw_body)') {
    throw "Unsupported signature canonicalization"
}

$utf8Strict = [Text.UTF8Encoding]::new($false, $true)
$vectorCount = 0
$seenAdapters = @{}
foreach ($vector in @($document.vectors)) {
    $vectorCount++
    if ([string]$vector.adapter -cne [string]$vector.source) {
        throw "Adapter and source must match in $($vector.name)"
    }
    if ([string]$vector.adapter -notin @('root_module', 've_tweak')) {
        throw "Unsupported adapter in $($vector.name): $($vector.adapter)"
    }
    if ([string]$vector.timestamp -notmatch '^\d+$') {
        throw "Timestamp must contain Unix epoch seconds in $($vector.name)"
    }
    if ([string]::IsNullOrWhiteSpace([string]$vector.nonce)) {
        throw "Nonce must not be empty in $($vector.name)"
    }
    $seenAdapters[[string]$vector.adapter] = $true
    Assert-NotPlaceholderSecret $vector.secret

    $bodyPath = Join-Path $RepositoryRoot $vector.body_file
    if (-not (Test-Path -LiteralPath $bodyPath -PathType Leaf)) {
        throw "Fixture body is missing: $($vector.body_file)"
    }
    $bodyBytes = [IO.File]::ReadAllBytes($bodyPath)
    if ($bodyBytes.Length -ge 3 -and $bodyBytes[0] -eq 0xEF -and $bodyBytes[1] -eq 0xBB -and $bodyBytes[2] -eq 0xBF) {
        throw "Fixture body must be UTF-8 without a BOM: $($vector.body_file)"
    }
    try {
        $bodyText = $utf8Strict.GetString($bodyBytes)
        $payload = $bodyText | ConvertFrom-Json
    } catch {
        throw "Fixture body is not valid UTF-8 JSON: $($vector.body_file)"
    }

    if ([string]$payload.source -cne [string]$vector.source) {
        throw "Source mismatch in $($vector.name)"
    }
    if ([string]$payload.client_message_id -cne [string]$vector.idempotency_key) {
        throw "Idempotency key does not match client_message_id in $($vector.name)"
    }
    try {
        if ($payload.received_at -is [DateTime]) {
            $receivedAt = [DateTimeOffset]$payload.received_at
        } else {
            $receivedAt = [DateTimeOffset]::Parse(
                [string]$payload.received_at,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::AssumeUniversal
            )
        }
    } catch {
        throw "received_at is not a valid timestamp in $($vector.name)"
    }
    if ([int64]$receivedAt.ToUnixTimeSeconds() -ne [int64]$vector.timestamp) {
        throw "timestamp does not match fixture received_at in $($vector.name)"
    }

    $actualBodyHash = Get-Sha256Hex $bodyBytes
    Assert-Equal -Label "$($vector.name) body SHA-256" -Actual $actualBodyHash -Expected ([string]$vector.body_sha256)

    $canonicalText = "{0}`n{1}`n{2}" -f [string]$vector.timestamp, [string]$vector.nonce, $actualBodyHash
    $canonicalBytes = [Text.Encoding]::UTF8.GetBytes($canonicalText)
    $secretBytes = ConvertFrom-Base64Url $vector.secret
    $hmac = [Security.Cryptography.HMACSHA256]::new($secretBytes)
    try {
        $actualSignature = ConvertTo-Base64Url ($hmac.ComputeHash($canonicalBytes))
    } finally {
        $hmac.Dispose()
    }
    Assert-Equal -Label "$($vector.name) signature" -Actual $actualSignature -Expected ([string]$vector.signature)
    Write-Output "PASS vector=$($vector.name) source=$($vector.source) body_bytes=$($bodyBytes.Length)"
}

if ($vectorCount -lt 2) {
    throw "At least two signature vectors are required"
}
foreach ($requiredAdapter in @('root_module', 've_tweak')) {
    if (-not $seenAdapters.ContainsKey($requiredAdapter)) {
        throw "Missing signature vector for $requiredAdapter"
    }
}

Invoke-RootContractHarness
$rootBehaviorPath = Join-Path $RepositoryRoot 'clients/root-module/tests/behavior.sh'
if (-not (Test-Path -LiteralPath $rootBehaviorPath -PathType Leaf)) {
    throw 'Root POSIX behavior harness is missing: clients/root-module/tests/behavior.sh'
}
$posixShellPath = $null
$posixToolPaths = @()
$gitCommand = Get-Command git -ErrorAction SilentlyContinue
if ($gitCommand) {
    $gitRoot = Split-Path (Split-Path $gitCommand.Source -Parent) -Parent
    $posixToolPaths = @((Join-Path $gitRoot 'usr\bin'), (Join-Path $gitRoot 'bin'))
    # Git for Windows keeps core utilities beside usr/bin/sh.exe. Prefer
    # that shell; bin/sh.exe alone may lack dirname/mktemp in PATH.
    foreach ($candidate in @((Join-Path $gitRoot 'usr\bin\sh.exe'), (Join-Path $gitRoot 'bin\sh.exe'))) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $posixShellPath = $candidate
            break
        }
    }
}
if (-not $posixShellPath) {
    $posixShell = Get-Command sh -ErrorAction SilentlyContinue
    if ($posixShell) { $posixShellPath = $posixShell.Source }
}
if (-not $posixShellPath) {
    Write-Output 'SKIP root_posix_runtime reason=missing_command:sh'
} else {
    $savedPath = $env:PATH
    try {
        if ($posixToolPaths.Count -gt 0) {
            $env:PATH = (($posixToolPaths + @($savedPath)) -join [System.IO.Path]::PathSeparator)
        }
        # Git for Windows strips its own installation directories while
        # translating the parent Windows PATH for a non-login shell. Set the
        # POSIX paths inside the shell so dirname/mktemp and the rest of the
        # core utilities are available consistently.
        & $posixShellPath -c 'PATH=/usr/bin:/bin:$PATH; export PATH; exec "$1"' sh $rootBehaviorPath
        if ($LASTEXITCODE -ne 0) {
            throw "Root POSIX behavior harness failed with exit code $LASTEXITCODE"
        }
    } finally {
        $env:PATH = $savedPath
    }
}

# The Root module is an executable shell entrypoint once Task 2 is present. Keep
# this check after vector validation so a RED result identifies the missing
# adapter artifact rather than hiding a bad protocol vector.
$rootUploader = Join-Path $RepositoryRoot 'clients/root-module/system/bin/sms-relay-upload'
if (-not (Test-Path -LiteralPath $rootUploader -PathType Leaf)) {
    throw "Adapter entrypoint absent: clients/root-module/system/bin/sms-relay-upload (vectors are valid; complete Task 2)"
}

$rootFiles = @(
    'clients/root-module/module.prop',
    'clients/root-module/service.sh',
    'clients/root-module/action.sh',
    'clients/root-module/config.example.json'
)
foreach ($relativePath in $rootFiles) {
    $path = Join-Path $RepositoryRoot $relativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Root artifact absent: $relativePath"
    }
}

$rootUploaderText = Get-Content -Raw -Encoding UTF8 -LiteralPath $rootUploader
$rootServiceText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $RepositoryRoot 'clients/root-module/service.sh')
$rootActionText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $RepositoryRoot 'clients/root-module/action.sh')
$rootModuleText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $RepositoryRoot 'clients/root-module/module.prop')
$rootConfigText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $RepositoryRoot 'clients/root-module/config.example.json')
$rootReadmePath = Join-Path $RepositoryRoot 'clients/root-module/README.md'
if (-not (Test-Path -LiteralPath $rootReadmePath -PathType Leaf)) {
    throw 'Root artifact absent: clients/root-module/README.md'
}
$rootReadmeText = Get-Content -Raw -Encoding UTF8 -LiteralPath $rootReadmePath
$rootRuntimePath = Join-Path $RepositoryRoot 'clients/root-module/native/runtime/main.go'
if (-not (Test-Path -LiteralPath $rootRuntimePath -PathType Leaf)) {
    throw 'Root artifact absent: clients/root-module/native/runtime/main.go'
}
$rootRuntimeText = Get-Content -Raw -Encoding UTF8 -LiteralPath $rootRuntimePath

$claimReviewFailures = [Collections.Generic.List[string]]::new()
if ($rootServiceText -notmatch 'INFLIGHT_DIR' -or
    $rootServiceText -notmatch 'claim_spool_message' -or
    $rootServiceText -notmatch 'process_inflight_claims') {
    $claimReviewFailures.Add('atomic_claim_missing')
}
if ($rootServiceText -notmatch 'SMS_RELAY_STATE_DIR' -or
    $rootServiceText -notmatch 'SMS_RELAY_LOCK_FILE' -or
    $rootServiceText -notmatch 'SMS_RELAY_UPLOADER') {
    $claimReviewFailures.Add('service_test_overrides_missing')
}
if ($rootUploaderText -notmatch 'SMS_RELAY_TMP_PARENT' -or
    $rootUploaderText -notmatch 'SMS_RELAY_STATE_DIR') {
    $claimReviewFailures.Add('uploader_test_overrides_missing')
}
$rootBehaviorText = Get-Content -Raw -Encoding UTF8 -LiteralPath $rootBehaviorPath
foreach ($behaviorEvidence in @(
    'claim-race',
    'body-16384',
    'body-16385',
    'depth-32',
    'depth-33',
    'collision',
    'link-failure',
    'delete-failure',
    'retry-recovery',
    'inflight-marker-between-uploads',
    'claim-marker-before-upload',
    'production-parser-negatives',
    'response-body-discard',
    'service-missing-dependency',
    'action-lifecycle lock-model=pid-lifecycle-with-legacy-lock-fallback'
)) {
    if ($rootBehaviorText -notmatch [regex]::Escape($behaviorEvidence)) {
        $claimReviewFailures.Add("behavior_case_missing:$behaviorEvidence")
    }
}
if ($rootReadmeText -notmatch '(?i)inflight|claim') {
    $claimReviewFailures.Add('claim_docs_missing')
}
if ($claimReviewFailures.Count -gt 0) {
    throw "Root claim gates failed: $($claimReviewFailures -join ', ')"
}

$machineOutcomeFailures = [Collections.Generic.List[string]]::new()
foreach ($requiredLocalOutcome in @('local_body_invalid', 'local_body_too_large', 'local_body_read_failed')) {
    if ($rootUploaderText -notmatch [regex]::Escape($requiredLocalOutcome) -or
        $rootServiceText -notmatch [regex]::Escape($requiredLocalOutcome)) {
        $machineOutcomeFailures.Add("missing_machine_outcome:$requiredLocalOutcome")
    }
}
if ($rootServiceText -match '400\|404\|409\|422\)' -or
    $rootServiceText -notmatch '409\|422\)') {
    $machineOutcomeFailures.Add('http_message_terminal_classification_wrong')
}
if ($rootServiceText -match 'mv\s+-f\s+"\$body_path"\s+"\$quarantine_target"' -or
    $rootServiceText -notmatch 'ln\s+"\$body_path"') {
    $machineOutcomeFailures.Add('quarantine_can_overwrite')
}
if ($machineOutcomeFailures.Count -gt 0) {
    throw "Root machine outcome gates failed: $($machineOutcomeFailures -join ', ')"
}

$latestRootReviewFailures = [Collections.Generic.List[string]]::new()
if ($rootServiceText -notmatch 'QUARANTINE_DIR' -or
    $rootServiceText -notmatch '409\|422\)' -or
    $rootServiceText -notmatch 'local_body_invalid\|local_body_too_large\|local_body_read_failed' -or
    $rootServiceText -notmatch 'quarantine_message') {
    $latestRootReviewFailures.Add('terminal_http_not_quarantined')
}
if ($rootUploaderText -notmatch 'RAW_BODY_MAX_BYTES=16384' -or
    $rootUploaderText -notmatch 'BODY_SNAPSHOT' -or
    $rootUploaderText -notmatch 'BODY_PATH=\$BODY_SNAPSHOT') {
    $latestRootReviewFailures.Add('body_not_snapshotted_or_bounded')
}
if ($rootUploaderText -notmatch 'JSON_MAX_DEPTH=32' -or
    $rootUploaderText -notmatch 'depth\s*>\s*max_depth') {
    $latestRootReviewFailures.Add('json_depth_unbounded')
}
$rootAttributesPath = Join-Path $RepositoryRoot 'clients/root-module/.gitattributes'
if (-not (Test-Path -LiteralPath $rootAttributesPath -PathType Leaf)) {
    $latestRootReviewFailures.Add('lf_attributes_missing')
} else {
    $rootAttributesText = Get-Content -Raw -Encoding UTF8 -LiteralPath $rootAttributesPath
    if ($rootAttributesText -notmatch '(?m)^\*\.sh\s+text\s+eol=lf\s*$' -or
        $rootAttributesText -notmatch '(?m)^system/bin/\*\s+text\s+eol=lf\s*$') {
        $latestRootReviewFailures.Add('lf_attributes_incomplete')
    }
}
if ($rootActionText -notmatch '(?m)^umask\s+077\s*$') {
    $latestRootReviewFailures.Add('action_umask_missing')
}
if ($rootUploaderText -match 'response\.txt' -or
    $rootUploaderText -notmatch '(?m)curl\s+-q\s+-sS\s+-o\s+/dev/null\s+-w\s+''%\{http_code\}''') {
    $latestRootReviewFailures.Add('response_body_not_discarded')
}
if ($rootUploaderText -notmatch '\*''\?''\*\|\*''#''\*') {
    $latestRootReviewFailures.Add('base_url_query_fragment_not_rejected')
}
foreach ($uploaderDependency in @('mktemp', 'od', 'dd', 'date')) {
    if ($rootServiceText -notmatch "(?m)^for dependency in [^`r`n]*\b$uploaderDependency\b") {
        $latestRootReviewFailures.Add("service_dependency_missing:$uploaderDependency")
    }
    if ($rootReadmeText -notmatch "(?i)``$uploaderDependency``") {
        $latestRootReviewFailures.Add("readme_dependency_missing:$uploaderDependency")
    }
}
if ($rootReadmeText -match 'spool_delete_failed' -or $rootReadmeText -notmatch 'claim_delete_failed') {
    $latestRootReviewFailures.Add('claim_delete_outcome_docs_wrong')
}
if ($rootBehaviorText -notmatch '"\$REAL_SH"\s+"\$ACTION"') {
    $latestRootReviewFailures.Add('action_not_executed_by_behavior_harness')
}
$lfCheckPaths = @(
    (Join-Path $RepositoryRoot 'clients/root-module/action.sh'),
    (Join-Path $RepositoryRoot 'clients/root-module/service.sh'),
    (Join-Path $RepositoryRoot 'clients/root-module/system/bin/sms-relay-upload'),
    $rootBehaviorPath
)
foreach ($lfPath in $lfCheckPaths) {
    $lfBytes = [IO.File]::ReadAllBytes($lfPath)
    if ($lfBytes -contains [byte]0x0D) {
        $latestRootReviewFailures.Add("crlf_present:$([IO.Path]::GetFileName($lfPath))")
    }
    if ($lfBytes.Length -eq 0 -or $lfBytes[$lfBytes.Length - 1] -ne [byte]0x0A) {
        $latestRootReviewFailures.Add("missing_final_lf:$([IO.Path]::GetFileName($lfPath))")
    }
}
if ($latestRootReviewFailures.Count -gt 0) {
    throw "Latest Root quality review gates failed: $($latestRootReviewFailures -join ', ')"
}

# Quality-review regression gates are aggregated so one RED run proves every
# reported defect before implementation changes begin.
$rootReviewFailures = [Collections.Generic.List[string]]::new()
if ($rootUploaderText -notmatch '\[\s*"\$CONFIG_ENABLED"\s*=\s*"true"\s*\]\s*\|\|\s*fail\s+"config_disabled"') {
    $rootReviewFailures.Add('config_enabled_not_enforced')
}
if ($rootUploaderText -notmatch 'validate_body_json' -or
    $rootUploaderText -notmatch 'require_command\s+awk' -or
    $rootUploaderText -match '(?m)^json_string_field\(\)') {
    $rootReviewFailures.Add('body_not_full_top_level_json')
}
$serviceLockMatch = [regex]::Match($rootServiceText, '(?m)^acquire_lock\s*$')
$serviceLockCall = if ($serviceLockMatch.Success) { $serviceLockMatch.Index } else { -1 }
$serviceMarkerCheck = $rootServiceText.IndexOf('[ ! -e "$DISABLED_MARKER" ] || exit 0', [StringComparison]::Ordinal)
$serviceConfigCheck = $rootServiceText.IndexOf('[ ! -f "$CONFIG_PATH" ]', [StringComparison]::Ordinal)
if ($serviceLockCall -lt 0 -or $serviceMarkerCheck -lt 0 -or $serviceConfigCheck -lt 0 -or
    $serviceLockCall -gt $serviceMarkerCheck -or $serviceLockCall -gt $serviceConfigCheck) {
    $rootReviewFailures.Add('service_lock_after_marker_or_config')
}
if ($rootReviewFailures.Count -gt 0) {
    throw "Root quality review gates failed: $($rootReviewFailures -join ', ')"
}

if (-not (Test-StrictFlatConfigText $rootConfigText)) {
    throw 'Root example config does not satisfy strict flat JSON contract'
}
$negativeConfigs = @{
    duplicate = $rootConfigText.Replace('  "enabled": false,', "  `"enabled`": false,`r`n  `"enabled`": true,")
    nested = $rootConfigText.Replace('  "base_url": "https://relay.example.invalid",', '  "base_url": {"value":"https://relay.example.invalid"},')
    array = $rootConfigText.Replace('  "source": "root_module",', '  "source": ["root_module"],')
    trueish = $rootConfigText.Replace('  "enabled": false,', '  "enabled": trueish,')
    decimal = $rootConfigText.Replace('  "poll_interval_seconds": 3,', '  "poll_interval_seconds": 1.5,')
}
foreach ($negativeName in $negativeConfigs.Keys) {
    if (Test-StrictFlatConfigText $negativeConfigs[$negativeName]) {
        throw "Strict config negative gate unexpectedly accepted: $negativeName"
    }
}

Assert-Contains -Label 'Root uploader shebang' -Text $rootUploaderText -Pattern '(?m)^#!/system/bin/sh\s*$'
Assert-Contains -Label 'Root uploader dependency helper' -Text $rootUploaderText -Pattern 'command -v\s+"\$1"'
Assert-Contains -Label 'Root uploader curl dependency check' -Text $rootUploaderText -Pattern 'require_command\s+curl'
Assert-Contains -Label 'Root uploader HTTPS guard' -Text $rootUploaderText -Pattern 'https://|HTTPS'
Assert-Contains -Label 'Root uploader UUID validation' -Text $rootUploaderText -Pattern '\[0-9A-Fa-f\]\{8\}'
Assert-Contains -Label 'Root uploader source contract' -Text $rootUploaderText -Pattern 'root_module'
Assert-Contains -Label 'Root uploader runtime SHA-256 call' -Text $rootUploaderText -Pattern '"\$RUNTIME"\s+sha256'
Assert-Contains -Label 'Root uploader runtime HMAC call' -Text $rootUploaderText -Pattern '"\$RUNTIME"\s+hmac'
Assert-Contains -Label 'Root runtime canonical HMAC' -Text $rootRuntimeText -Pattern 'hmac\.New\(sha256\.New'
Assert-Contains -Label 'Root runtime base64url handling' -Text $rootRuntimeText -Pattern 'base64\.RawURLEncoding'
Assert-Contains -Label 'Root runtime 32-byte secret validation' -Text $rootRuntimeText -Pattern 'len\(secret\)\s*!=\s*32'
Assert-Contains -Label 'Root runtime repeated-byte secret rejection' -Text $rootRuntimeText -Pattern 'allSame'
Assert-Contains -Label 'Root runtime ASCII marker rejection' -Text $rootRuntimeText -Pattern '(?s)"placeholder".*"change_me".*"not_enrolled".*"todo".*"xxx"'
Assert-Contains -Label 'Root uploader protocol headers' -Text $rootUploaderText -Pattern 'X-Device-Id'
Assert-Contains -Label 'Root uploader protocol headers' -Text $rootUploaderText -Pattern 'X-Timestamp'
Assert-Contains -Label 'Root uploader protocol headers' -Text $rootUploaderText -Pattern 'X-Nonce'
Assert-Contains -Label 'Root uploader protocol headers' -Text $rootUploaderText -Pattern 'X-Signature'
Assert-Contains -Label 'Root uploader protocol headers' -Text $rootUploaderText -Pattern 'X-Idempotency-Key'
Assert-Contains -Label 'Root uploader exact-body upload' -Text $rootUploaderText -Pattern '--data-binary\s+"@\$BODY_PATH"'
Assert-Contains -Label 'Root uploader status-only output' -Text $rootUploaderText -Pattern 'status|HTTP'
Assert-Contains -Label 'Root uploader curlrc disabled first' -Text $rootUploaderText -Pattern '(?m)curl\s+-q\s+-sS|curl\s+--disable\s+-sS'
Assert-Contains -Label 'Root uploader runtime probe' -Text $rootUploaderText -Pattern '(?i)runtime_probe|probe.*runtime|runtime.*probe'
Assert-Contains -Label 'Root uploader runtime curl flag probe' -Text $rootUploaderText -Pattern '--help\s+all|curl_flag'
Assert-Contains -Label 'Root uploader raw body cap' -Text $rootUploaderText -Pattern 'RAW_BODY_MAX_BYTES=16384'
Assert-Contains -Label 'Root uploader private body snapshot' -Text $rootUploaderText -Pattern 'BODY_SNAPSHOT|dd\s+if="\$BODY_PATH"\s+of="\$BODY_SNAPSHOT"'
Assert-Contains -Label 'Root uploader snapshot size gate' -Text $rootUploaderText -Pattern 'snapshot_size|body_too_large'
Assert-Contains -Label 'Root uploader depth cap' -Text $rootUploaderText -Pattern 'JSON_MAX_DEPTH=32|max_depth'
Assert-Contains -Label 'Root uploader machine body failure helper' -Text $rootUploaderText -Pattern '(?s)fail_body\(\).*?printf[^\r\n]*"\$1"'
foreach ($localOutcome in @('local_body_invalid', 'local_body_too_large', 'local_body_read_failed')) {
    Assert-Contains -Label "Root uploader outcome $localOutcome" -Text $rootUploaderText -Pattern ([regex]::Escape($localOutcome))
    Assert-Contains -Label "Root service outcome $localOutcome" -Text $rootServiceText -Pattern ([regex]::Escape($localOutcome))
}
Assert-Contains -Label 'Root uploader disabled config rejection' -Text $rootUploaderText -Pattern '\[\s*"\$CONFIG_ENABLED"\s*=\s*"true"\s*\]\s*\|\|\s*fail\s+"config_disabled"'
Assert-Contains -Label 'Root uploader awk dependency' -Text $rootUploaderText -Pattern 'require_command\s+awk'
Assert-Contains -Label 'Root uploader full body JSON parser' -Text $rootUploaderText -Pattern 'validate_body_json'
Assert-Contains -Label 'Root uploader object root parser' -Text $rootUploaderText -Pattern 'parse_object\(1(?:,\s*1)?\)'
Assert-Contains -Label 'Root uploader top-level contract counters' -Text $rootUploaderText -Pattern 'source_count\s*!=\s*1\s*\|\|\s*id_count\s*!=\s*1'
Assert-Contains -Label 'Root uploader complete document check' -Text $rootUploaderText -Pattern 'pos\s*<=\s*text_len'
Assert-NotContains -Label 'Root uploader grep-only body parser' -Text $rootUploaderText -Pattern '(?m)^json_string_field\(\)'
Assert-Contains -Label 'Root uploader local HTTP opt-in' -Text $rootUploaderText -Pattern 'ALLOW_INSECURE_LOCAL_HTTP'
Assert-Contains -Label 'Root runtime private HTTP guard' -Text $rootRuntimeText -Pattern 'IsPrivate\(\).*IsLoopback\(\)'
Assert-NotContains -Label 'Root uploader shell tracing' -Text $rootUploaderText -Pattern '(?m)^\s*set\s+-[^\r\n]*x'
Assert-NotContains -Label 'Root uploader system logging' -Text $rootUploaderText -Pattern '(?i)\blogger\b'
Assert-NotContains -Label 'Root uploader verbose curl' -Text $rootUploaderText -Pattern '(?i)curl[^\r\n]*(--verbose|\s-v(?:\s|$))'
Assert-Contains -Label 'Root uploader exiting HUP trap' -Text $rootUploaderText -Pattern "trap\s+'exit\s+129'\s+HUP"
Assert-Contains -Label 'Root uploader exiting INT trap' -Text $rootUploaderText -Pattern "trap\s+'exit\s+130'\s+INT"
Assert-Contains -Label 'Root uploader exiting TERM trap' -Text $rootUploaderText -Pattern "trap\s+'exit\s+143'\s+TERM"
Assert-Contains -Label 'Root uploader single EXIT cleanup trap' -Text $rootUploaderText -Pattern 'trap\s+cleanup\s+EXIT'
Assert-NotContains -Label 'Root uploader non-exiting signal cleanup' -Text $rootUploaderText -Pattern 'trap\s+cleanup\s+EXIT\s+HUP'

Assert-Contains -Label 'Root service directory lock fallback' -Text $rootServiceText -Pattern 'acquire_directory_lock'
Assert-Contains -Label 'Root service fixed fd lock' -Text $rootServiceText -Pattern 'exec\s+9>'
Assert-Contains -Label 'Root service nonblocking flock' -Text $rootServiceText -Pattern 'flock\s+-n\s+9'
Assert-Contains -Label 'Root service fixed early lock path' -Text $rootServiceText -Pattern 'LOCK_FILE=\$\{SMS_RELAY_LOCK_FILE:-/data/adb/sms-relay\.worker\.lock\}'
Assert-Contains -Label 'Root service inflight directory' -Text $rootServiceText -Pattern 'INFLIGHT_DIR=\$RUN_DIR/inflight'
Assert-Contains -Label 'Root service unique claim directory' -Text $rootServiceText -Pattern 'CLAIM_DIR=\$INFLIGHT_DIR/claim\.\$\$\.\$CLAIM_COUNTER'
Assert-Contains -Label 'Root service atomic claim move' -Text $rootServiceText -Pattern 'mv\s+"\$spool_path"\s+"\$CLAIM_PATH"'
Assert-Contains -Label 'Root service processes claimed path' -Text $rootServiceText -Pattern 'process_claim_message\s+"\$CLAIM_PATH"'
Assert-Contains -Label 'Root service recovers inflight claims' -Text $rootServiceText -Pattern '"\$INFLIGHT_DIR"/\*/\*\.json'
Assert-NotContains -Label 'Root service uploads original spool path' -Text $rootServiceText -Pattern '"\$UPLOADER"[^\r\n]*"\$spool_path"'
Assert-NotContains -Label 'Root service legacy standalone PID lock' -Text $rootServiceText -Pattern 'owner\.pid|LOCK_OWNER|recover_stale_lock'
Assert-Contains -Label 'Root service directory lock PID identity check' -Text $rootServiceText -Pattern 'kill\s+-0.*pid_is_service'
Assert-Contains -Label 'Root service signal exit handler' -Text $rootServiceText -Pattern '(?s)handle_signal\(\).*?exit'
Assert-Contains -Label 'Root service signal traps' -Text $rootServiceText -Pattern "trap\s+'handle_signal\s+129'\s+HUP"
Assert-Contains -Label 'Root service exit cleanup' -Text $rootServiceText -Pattern 'trap\s+cleanup\s+EXIT'
Assert-NotContains -Label 'Root service non-exiting signal cleanup' -Text $rootServiceText -Pattern 'trap\s+cleanup\s+EXIT\s+HUP'
Assert-Contains -Label 'Root service marker recheck before reader' -Text $rootServiceText -Pattern '(?s)\[ -e "\$DISABLED_MARKER" \].*?run_reader'
Assert-Contains -Label 'Root service marker recheck after reader' -Text $rootServiceText -Pattern '(?s)run_reader.*?\[ -e "\$DISABLED_MARKER" \]'
Assert-Contains -Label 'Root service reader runtime launch' -Text $rootServiceText -Pattern '(?s)run_reader\(\).*?"\$RUNTIME"\s+read'
Assert-Contains -Label 'Root service reader pid lifecycle' -Text $rootServiceText -Pattern '(?s)run_reader\(\).*?READER_PID_PATH.*?wait\s+"\$reader_pid".*?rm\s+-f\s+"\$READER_PID_PATH"'
Assert-Contains -Label 'Root service marker recheck before claim' -Text $rootServiceText -Pattern 'spool_path[^\r\n]*\n[^\r\n]*\[ -e "\$DISABLED_MARKER" \]'
Assert-Contains -Label 'Root service failed claim delete handling' -Text $rootServiceText -Pattern 'claim_delete_failed'
Assert-Contains -Label 'Root service terminal HTTP cases' -Text $rootServiceText -Pattern '409\|422\)'
Assert-Contains -Label 'Root service quarantine path' -Text $rootServiceText -Pattern 'QUARANTINE_DIR|quarantine_message|quarantine_failed'
Assert-Contains -Label 'Root service quarantine no-clobber link' -Text $rootServiceText -Pattern 'ln\s+"\$body_path"\s+"\$quarantine_target"'
Assert-Contains -Label 'Root service quarantine collision suffix' -Text $rootServiceText -Pattern 'quarantine_basename\.\$quarantine_index'
Assert-NotContains -Label 'Root service quarantine overwrite' -Text $rootServiceText -Pattern 'mv\s+-f\s+"\$body_path"\s+"\$quarantine_target"'
Assert-NotContains -Label 'Root service unverified 400 terminal directory' -Text $rootServiceText -Pattern 'http_400_terminal|http_404_terminal'
Assert-Contains -Label 'Root service terminal statuses use quarantine' -Text $rootServiceText -Pattern '409\|422\|local_body_invalid\|local_body_too_large\|local_body_read_failed\)\s*\r?\n\s*(?:if\s+!\s+)?quarantine_message'
Assert-Contains -Label 'Root service backoff' -Text $rootServiceText -Pattern '(?i)backoff|exponential'
Assert-Contains -Label 'Root service owned reader runtime' -Text $rootServiceText -Pattern '"\$RUNTIME"\s+read'
Assert-Contains -Label 'Root service root_module source' -Text $rootServiceText -Pattern 'root_module'
Assert-NotContains -Label 'Root service insecure URL support' -Text $rootServiceText -Pattern 'http://'

$verifiedServiceLock = [regex]::Match($rootServiceText, '(?m)^acquire_lock\s*$')
if (-not $verifiedServiceLock.Success -or
    $verifiedServiceLock.Index -gt $rootServiceText.IndexOf('[ ! -e "$DISABLED_MARKER" ] || exit 0', [StringComparison]::Ordinal) -or
    $verifiedServiceLock.Index -gt $rootServiceText.IndexOf('[ ! -f "$CONFIG_PATH" ]', [StringComparison]::Ordinal)) {
    throw 'Root service must acquire worker lock before marker and config checks'
}
$inflightCallMatch = [regex]::Match($rootServiceText, '(?m)^\s*process_inflight_claims\s*$')
$inflightCallIndex = if ($inflightCallMatch.Success) { $inflightCallMatch.Index } else { -1 }
$readerCallMatch = [regex]::Match($rootServiceText, '(?m)^\s*run_reader\s*\|\|\s*reader_failed=1\s*$')
$readerCallIndex = if ($readerCallMatch.Success) { $readerCallMatch.Index } else { -1 }
if ($inflightCallIndex -lt 0 -or $readerCallIndex -lt 0 -or $inflightCallIndex -gt $readerCallIndex) {
    throw 'Root service must process inflight claims before invoking reader'
}

Assert-Contains -Label 'Root action lifecycle' -Text $rootActionText -Pattern '(?i)start|stop|enable|disable'
Assert-Contains -Label 'Root action restrictive umask' -Text $rootActionText -Pattern '(?m)^umask\s+077\s*$'
Assert-Contains -Label 'Root action optional lock probe' -Text $rootActionText -Pattern 'worker_lock_is_free'
Assert-Contains -Label 'Root action legacy lock probe' -Text $rootActionText -Pattern 'flock\s+-n\s+9'
Assert-Contains -Label 'Root action PID lifecycle probe' -Text $rootActionText -Pattern 'WORKER_PID_PATH|worker_is_running'
Assert-Contains -Label 'Root action shared early lock path' -Text $rootActionText -Pattern 'LOCK_FILE=\$\{SMS_RELAY_LOCK_FILE:-/data/adb/sms-relay\.worker\.lock\}'
Assert-Contains -Label 'Root action state test override' -Text $rootActionText -Pattern 'STATE_DIR=\$\{SMS_RELAY_STATE_DIR:-/data/adb/sms-relay\}'
Assert-Contains -Label 'Root action bounded wait test override' -Text $rootActionText -Pattern 'WAIT_SECONDS=\$\{SMS_RELAY_WAIT_SECONDS:-30\}'
Assert-Contains -Label 'Root action bounded stop wait' -Text $rootActionText -Pattern '(?i)stop_pending|revoke_pending|timeout'
Assert-Contains -Label 'Root action start checks current worker' -Text $rootActionText -Pattern '(?s)start\|enable\).*?WORKER_PID_PATH.*?worker_lock_is_free'
Assert-Contains -Label 'Root action start launches worker' -Text $rootActionText -Pattern '(?s)start\|enable\).*?start_worker'
Assert-Contains -Label 'Root action stop waits before success' -Text $rootActionText -Pattern '(?s)stop\|disable\).*?write_disabled_marker.*?wait_for_worker_exit.*?"disabled"'
Assert-Contains -Label 'Root action revoke waits before success' -Text $rootActionText -Pattern '(?s)revoke\).*?write_disabled_marker.*?wait_for_worker_exit.*?"credential_revocation_required"'
Assert-Contains -Label 'Root module id' -Text $rootModuleText -Pattern '(?m)^id=sms-relay-root$'
Assert-Contains -Label 'Root module name' -Text $rootModuleText -Pattern '(?m)^name='
Assert-Contains -Label 'Root config source' -Text $rootConfigText -Pattern '"source"\s*:\s*"root_module"'
Assert-Contains -Label 'Root config secret field' -Text $rootConfigText -Pattern '"hmac_secret"'
Assert-Contains -Label 'Root config built-in reader field' -Text $rootConfigText -Pattern '"android_user_id"'
Assert-Contains -Label 'Root config local HTTP opt-in field' -Text $rootConfigText -Pattern '"allow_insecure_local_http"'
Assert-Contains -Label 'Root strict config parser' -Text $rootServiceText -Pattern '(?i)strict|parse_config|duplicate|nested|top.level'
Assert-Contains -Label 'Root strict config parser uploader' -Text $rootUploaderText -Pattern '(?i)strict|parse_config|duplicate|nested|top.level'
Assert-NotContains -Label 'Root service loose flattened parser' -Text $rootServiceText -Pattern 'json_flatten|tr\s+''\\r\\n\\t'''
Assert-NotContains -Label 'Root uploader loose flattened config parser' -Text $rootUploaderText -Pattern 'json_flatten'
Assert-Contains -Label 'Root service scalar parser' -Text $rootServiceText -Pattern '(?m)^parse_scalar_token\(\)\s*\{'
Assert-Contains -Label 'Root service boolean type validation' -Text $rootServiceText -Pattern 'true\|false\)'
Assert-Contains -Label 'Root service integer type validation' -Text $rootServiceText -Pattern '0\|\[1-9\]\|\[1-9\]\[0-9\]\*'
Assert-Contains -Label 'Root service scalar type assignment' -Text $rootServiceText -Pattern 'value_type=string|value_type=boolean|value_type=integer'
Assert-Contains -Label 'Root uploader expected config types' -Text $rootUploaderText -Pattern 'enabled\).*value_type[^\r\n]*boolean'
Assert-Contains -Label 'Root uploader duplicate config rejection' -Text $rootUploaderText -Pattern 'seen_enabled[^\r\n]*= 0|seen_enabled[^\r\n]*=0'
Assert-Contains -Label 'Root dependency list rm' -Text $rootReadmeText -Pattern '(?i)`rm`'
Assert-Contains -Label 'Root dependency list mv' -Text $rootReadmeText -Pattern '(?i)`mv`'
Assert-Contains -Label 'Root dependency list chmod' -Text $rootReadmeText -Pattern '(?i)`chmod`'
Assert-Contains -Label 'Root dependency list ln' -Text $rootReadmeText -Pattern '(?i)`ln`'
Assert-Contains -Label 'Root dependency list rmdir' -Text $rootReadmeText -Pattern '(?i)`rmdir`'
Assert-Contains -Label 'Root dependency list lock fallback' -Text $rootReadmeText -Pattern '(?i)flock.*目录锁|目录锁.*flock'
Assert-Contains -Label 'Root dependency list awk' -Text $rootReadmeText -Pattern '(?i)`awk`'
Assert-Contains -Label 'Root quarantine docs' -Text $rootReadmeText -Pattern '(?i)400.*404.*409.*422|quarantine'
Assert-Contains -Label 'Root machine outcome docs' -Text $rootReadmeText -Pattern 'local_body_invalid.*local_body_too_large|machine-readable'
Assert-Contains -Label 'Root quarantine collision docs' -Text $rootReadmeText -Pattern '(?i)never overwrites|collisions receive|quarantine.*overwrite'
Assert-Contains -Label 'Root 400 worker-level docs' -Text $rootReadmeText -Pattern '(?i)400.*404.*worker-level'
Assert-Contains -Label 'Root snapshot docs' -Text $rootReadmeText -Pattern '(?i)16 KiB|16KB|16384'
Assert-Contains -Label 'Root JSON depth docs' -Text $rootReadmeText -Pattern '(?i)32 levels|depth'
Assert-Contains -Label 'Root disabled config docs' -Text $rootReadmeText -Pattern 'config_disabled'
Assert-Contains -Label 'Root top-level JSON body docs' -Text $rootReadmeText -Pattern '(?i)unique top-level|string.*top-level|top-level string'
Assert-Contains -Label 'Root config atomic replacement docs' -Text $rootReadmeText -Pattern '(?i)atomic|\bmv\b|temporary'
Assert-Contains -Label 'Root stop success required docs' -Text $rootReadmeText -Pattern '(?i)stop.*成功|stop.*success|stop_pending'
Assert-Contains -Label 'Root claim lifecycle docs' -Text $rootReadmeText -Pattern '(?i)run/inflight|unique.*claim|claim.*spool'
Assert-Contains -Label 'Root behavior harness docs' -Text $rootReadmeText -Pattern 'tests/behavior\.sh|root_posix_runtime'
Assert-Contains -Label 'Root behavior harness production execution' -Text $rootBehaviorText -Pattern '"\$REAL_SH"\s+"\$SERVICE"|"\$REAL_SH"\s+"\$UPLOADER"'
Assert-Contains -Label 'Root behavior harness race coverage' -Text $rootBehaviorText -Pattern 'HARNESS_REPLACE_ON_CLAIM|claim-race'
Assert-Contains -Label 'Root behavior harness failure coverage' -Text $rootBehaviorText -Pattern 'HARNESS_FAIL_LINK|HARNESS_FAIL_CLAIM_DELETE_BASENAME'

foreach ($platform in @('Magisk', 'KernelSU', 'APatch')) {
    Assert-Contains -Label 'Root README platform documentation' -Text $rootReadmeText -Pattern $platform
}
Assert-Contains -Label 'Root README external acceptance boundary' -Text $rootReadmeText -Pattern '(?i)source|static'
Assert-Contains -Label 'Root README external acceptance boundary' -Text $rootReadmeText -Pattern '(?i)真机|real device|external'

$veRelativeFiles = @(
    'clients/ve-tweak/SMSRelayClient.h',
    'clients/ve-tweak/SMSRelayClient.m',
    'clients/ve-tweak/VeSMSRelay.xm',
    'clients/ve-tweak/Makefile',
    'clients/ve-tweak/README.md',
    'clients/ve-tweak/COPYING',
    'clients/ve-tweak/patches/README.md',
    'clients/ve-tweak/patches/ve-core-2.3.4-sms-relay.patch'
)
foreach ($relativePath in $veRelativeFiles) {
    $path = Join-Path $RepositoryRoot $relativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Ve artifact absent: $relativePath (vectors are valid; complete Task 3)"
    }
    Assert-AsciiUtf8LfFile -Label "Ve artifact $relativePath" -Path $path
}

$veHeaderText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $RepositoryRoot 'clients/ve-tweak/SMSRelayClient.h')
$veClientText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $RepositoryRoot 'clients/ve-tweak/SMSRelayClient.m')
$veHookText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $RepositoryRoot 'clients/ve-tweak/VeSMSRelay.xm')
$veMakefileText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $RepositoryRoot 'clients/ve-tweak/Makefile')
$veReadmeText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $RepositoryRoot 'clients/ve-tweak/README.md')
$veIntegrationPatchText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $RepositoryRoot 'clients/ve-tweak/patches/ve-core-2.3.4-sms-relay.patch')
$veIntegrationReadmeText = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $RepositoryRoot 'clients/ve-tweak/patches/README.md')

Assert-Contains -Label 'Ve public shared client' -Text $veHeaderText -Pattern '\+\s*\(instancetype\)sharedClient'
Assert-Contains -Label 'Ve public enabled probe' -Text $veHeaderText -Pattern '\+\s*\(BOOL\)isEnabled'
Assert-Contains -Label 'Ve public relay method' -Text $veHeaderText -Pattern 'relayMessageFromSender:\s*\(NSString\s*\*\)sender\s+body:\s*\(NSString\s*\*\)body\s+date:\s*\(NSDate\s*\*\)date'
Assert-Contains -Label 'Ve Foundation import' -Text $veClientText -Pattern '#import\s+<Foundation/Foundation\.h>'
Assert-Contains -Label 'Ve CommonDigest import' -Text $veClientText -Pattern '#import\s+<CommonCrypto/CommonDigest\.h>'
Assert-Contains -Label 'Ve CommonHMAC import' -Text $veClientText -Pattern '#import\s+<CommonCrypto/CommonHMAC\.h>'
Assert-Contains -Label 'Ve Security import' -Text $veClientText -Pattern '#import\s+<Security/Security\.h>'

Assert-Contains -Label 'Ve preference path' -Text $veClientText -Pattern '/var/mobile/Library/Preferences/com\.smsrelay\.ve\.plist'
foreach ($preferenceKey in @('Enabled', 'BaseURL', 'DeviceID', 'HMACSecret')) {
    Assert-Contains -Label "Ve preference key $preferenceKey" -Text $veClientText -Pattern ([regex]::Escape((' @"' + $preferenceKey + '"').TrimStart()))
    Assert-Contains -Label "Ve README preference key $preferenceKey" -Text $veReadmeText -Pattern $preferenceKey
}
Assert-Contains -Label 'Ve exact boolean enabled validation' -Text $veClientText -Pattern 'CFBooleanGetTypeID\(\)'
Assert-Contains -Label 'Ve fail-closed enabled validation' -Text $veClientText -Pattern '!enabled\.boolValue'
Assert-Contains -Label 'Ve preference descriptor open' -Text $veClientText -Pattern 'open\(kSMSRelayPreferencePath\.fileSystemRepresentation,\s*O_RDONLY\s*\|\s*O_NOFOLLOW\s*\|\s*O_CLOEXEC\s*\|\s*O_NONBLOCK\)'
Assert-Contains -Label 'Ve preference descriptor stat' -Text $veClientText -Pattern 'fstat\(descriptor,\s*&fileInfo\)'
Assert-Contains -Label 'Ve preference regular file validation' -Text $veClientText -Pattern 'S_ISREG\(fileInfo\.st_mode\)'
Assert-Contains -Label 'Ve preference owner validation' -Text $veClientText -Pattern 'fileInfo\.st_uid\s*!=\s*geteuid\(\)'
Assert-Contains -Label 'Ve preference group and other mode validation' -Text $veClientText -Pattern 'fileInfo\.st_mode\s*&\s*\(S_IRWXG\s*\|\s*S_IRWXO\)'
Assert-Contains -Label 'Ve preference size limit' -Text $veClientText -Pattern 'kSMSRelayMaxPreferenceBytes\s*=\s*16384'
Assert-Contains -Label 'Ve preference descriptor read' -Text $veClientText -Pattern 'read\(descriptor,'
Assert-Contains -Label 'Ve preference plist parser' -Text $veClientText -Pattern 'NSPropertyListSerialization\s+propertyListWithData:preferenceData'
Assert-Contains -Label 'Ve preference secure reader use' -Text $veClientText -Pattern 'preferences\s*=\s*SMSRelayReadPreferences\(\)'
Assert-NotContains -Label 'Ve preference path reread forbidden' -Text $veClientText -Pattern 'dictionaryWithContentsOfFile'
Assert-Contains -Label 'Ve HTTPS scheme validation' -Text $veClientText -Pattern 'caseInsensitiveCompare:@"https"'
Assert-Contains -Label 'Ve nonempty host validation' -Text $veClientText -Pattern 'components\.host\.length\s*==\s*0'
foreach ($forbiddenComponent in @('user', 'password', 'query', 'fragment')) {
    Assert-Contains -Label "Ve URL rejects $forbiddenComponent" -Text $veClientText -Pattern ([regex]::Escape("components.$forbiddenComponent != nil"))
}
Assert-Contains -Label 'Ve UUID validation' -Text $veClientText -Pattern 'initWithUUIDString:deviceID'
Assert-Contains -Label 'Ve canonical base64url alphabet' -Text $veClientText -Pattern '\^\[A-Za-z0-9_-\]\+\$'
Assert-Contains -Label 'Ve canonical base64url round trip' -Text $veClientText -Pattern '!\[\[self base64URLEncodedString:secretData\] isEqualToString:secret\]'
Assert-Contains -Label 'Ve 32-byte secret validation' -Text $veClientText -Pattern 'secretData\.length\s*!=\s*32'
Assert-Contains -Label 'Ve raw secret normalization' -Text $veClientText -Pattern 'secretLower\s*=\s*\[secret lowercaseString\]'
Assert-Contains -Label 'Ve raw secret exact sentinel set' -Text $veClientText -Pattern 'rawSecretSentinels\s*=\s*\[NSSet setWithArray:'
Assert-Contains -Label 'Ve raw secret exact sentinel rejection' -Text $veClientText -Pattern '\[rawSecretSentinels containsObject:secretLower\]'
Assert-Contains -Label 'Ve decoded secret placeholder scan' -Text $veClientText -Pattern 'decodedLower\s*=\s*\[decodedText lowercaseString\]'
$veRawSecretStart = $veClientText.IndexOf('NSString *secretLower = [secret lowercaseString];', [StringComparison]::Ordinal)
$veSecretDecodeStart = $veClientText.IndexOf('NSData *secretData = SMSRelayDataFromBase64URL(secret);', [StringComparison]::Ordinal)
if ($veRawSecretStart -lt 0 -or $veSecretDecodeStart -le $veRawSecretStart) {
    throw 'Ve raw secret validation boundaries are missing'
}
$veRawSecretValidationText = $veClientText.Substring($veRawSecretStart, $veSecretDecodeStart - $veRawSecretStart)
Assert-NotContains -Label 'Ve raw secret substring rejection forbidden' -Text $veRawSecretValidationText -Pattern 'containsString:'
Assert-Contains -Label 'Ve decoded placeholder substring scan' -Text $veClientText -Pattern '\[decodedLower containsString:marker\]'
Assert-Contains -Label 'Ve all-zero secret rejection' -Text $veClientText -Pattern 'allZero'
Assert-Contains -Label 'Ve repeated-byte secret rejection' -Text $veClientText -Pattern 'allSame'
foreach ($placeholderMarker in @('placeholder', 'change_me', 'changeme', 'replace_me', 'replace-me', 'your_secret', 'your-secret', 'not_enrolled', 'todo', 'xxx')) {
    Assert-Contains -Label "Ve placeholder rejection $placeholderMarker" -Text $veClientText -Pattern ([regex]::Escape((' @"' + $placeholderMarker + '"').TrimStart()))
}

Assert-Contains -Label 'Ve source constant' -Text $veClientText -Pattern 'kSMSRelaySource\s*=\s*@"ve_tweak"'
Assert-Contains -Label 'Ve source contract' -Text $veClientText -Pattern '@"source"\s*:\s*kSMSRelaySource'
Assert-Contains -Label 'Ve schema version' -Text $veClientText -Pattern '@"schema_version"\s*:\s*@1'
Assert-Contains -Label 'Ve metadata client version only' -Text $veClientText -Pattern '@"metadata"\s*:\s*@\{\s*@"client_version"\s*:'
Assert-NotContains -Label 'Ve metadata sensitive fields' -Text $veClientText -Pattern '@"metadata"\s*:\s*@\{[^\}]*@"(sender|body|secret|signature|device_id)"'
Assert-Contains -Label 'Ve sender maximum' -Text $veClientText -Pattern 'kSMSRelayMaxSenderLength\s*=\s*200'
Assert-Contains -Label 'Ve body maximum' -Text $veClientText -Pattern 'kSMSRelayMaxBodyLength\s*=\s*4096'
Assert-Contains -Label 'Ve client id maximum' -Text $veClientText -Pattern 'kSMSRelayMaxClientMessageIDLength\s*=\s*200'
Assert-Contains -Label 'Ve nonempty sender validation' -Text $veClientText -Pattern 'sender\.length\s*==\s*0'
Assert-Contains -Label 'Ve nonempty body validation' -Text $veClientText -Pattern 'body\.length\s*==\s*0'
Assert-Contains -Label 'Ve UTC timestamp formatter' -Text $veClientText -Pattern 'timeZoneForSecondsFromGMT:0'

Assert-Contains -Label 'Ve stable id sender bytes' -Text $veClientText -Pattern 'senderData\s*=\s*\[sender dataUsingEncoding:NSUTF8StringEncoding\]'
Assert-Contains -Label 'Ve stable id body bytes' -Text $veClientText -Pattern 'bodyData\s*=\s*\[body dataUsingEncoding:NSUTF8StringEncoding\]'
Assert-Contains -Label 'Ve stable id SMS date source' -Text $veClientText -Pattern 'dateString\s*=\s*SMSRelayUTCString\(date\)'
Assert-Contains -Label 'Ve stable id SMS date bytes' -Text $veClientText -Pattern 'dateData\s*=\s*\[dateString dataUsingEncoding:NSUTF8StringEncoding\]'
Assert-Contains -Label 'Ve stable id prefix' -Text $veClientText -Pattern '\[@"ve_" stringByAppendingString:hex\]'
Assert-Contains -Label 'Ve stable id SHA-256' -Text $veClientText -Pattern 'CC_SHA256\(identityData\.bytes'
$veStableMethodStart = $veClientText.LastIndexOf('- (NSString *)stableClientMessageIDForSender:', [StringComparison]::Ordinal)
$veRelayMethodStart = $veClientText.IndexOf('- (void)relayMessageFromSender:', $veStableMethodStart, [StringComparison]::Ordinal)
if ($veStableMethodStart -lt 0 -or $veRelayMethodStart -le $veStableMethodStart) {
    throw 'Ve stable id method boundaries are missing'
}
$veStableMethodText = $veClientText.Substring($veStableMethodStart, $veRelayMethodStart - $veStableMethodStart)
Assert-NotContains -Label 'Ve stable id random nonce dependency' -Text $veStableMethodText -Pattern 'SecRandomCopyBytes|\[NSDate date\]'

Assert-Contains -Label 'Ve sorted compact JSON' -Text $veClientText -Pattern 'dataWithJSONObject:payload\s+options:NSJSONWritingSortedKeys'
Assert-Contains -Label 'Ve body hash uses raw body' -Text $veClientText -Pattern 'sha256HexForData:rawBody'
Assert-Contains -Label 'Ve HTTP body uses same raw body' -Text $veClientText -Pattern 'request\.HTTPBody\s*=\s*rawBody'
Assert-Contains -Label 'Ve send-time timestamp' -Text $veClientText -Pattern '\[\[NSDate date\] timeIntervalSince1970\]'
Assert-Contains -Label 'Ve secure nonce' -Text $veClientText -Pattern 'SecRandomCopyBytes\(kSecRandomDefault'
Assert-Contains -Label 'Ve body SHA-256' -Text $veClientText -Pattern 'CC_SHA256\('
Assert-Contains -Label 'Ve HMAC SHA-256' -Text $veClientText -Pattern 'CCHmac\(kCCHmacAlgSHA256'
Assert-Contains -Label 'Ve canonical HMAC text' -Text $veClientText -Pattern 'componentsJoinedByString:@"\\n"'
Assert-Contains -Label 'Ve base64url no padding' -Text $veClientText -Pattern 'stringByTrimmingCharactersInSet:\[NSCharacterSet characterSetWithCharactersInString:@"="\]'

foreach ($headerName in @('Content-Type', 'X-Device-Id', 'X-Timestamp', 'X-Nonce', 'X-Signature', 'X-Idempotency-Key')) {
    Assert-Contains -Label "Ve protocol header $headerName" -Text $veClientText -Pattern ([regex]::Escape((' @"' + $headerName + '"').TrimStart()))
}
Assert-Contains -Label 'Ve endpoint path' -Text $veClientText -Pattern '@"/api/v1/ingest/messages"'
Assert-Contains -Label 'Ve ephemeral URL session' -Text $veClientText -Pattern 'ephemeralSessionConfiguration'
Assert-Contains -Label 'Ve no URL cache' -Text $veClientText -Pattern 'configuration\.URLCache\s*=\s*nil'
Assert-Contains -Label 'Ve reload ignoring cache' -Text $veClientText -Pattern 'NSURLRequestReloadIgnoringLocalCacheData'
Assert-Contains -Label 'Ve redirect rejection delegate' -Text $veClientText -Pattern 'willPerformHTTPRedirection:'
Assert-Contains -Label 'Ve redirect rejection result' -Text $veClientText -Pattern 'completionHandler\(nil\)'
Assert-Contains -Label 'Ve session uses redirect delegate' -Text $veClientText -Pattern 'sessionWithConfiguration:sessionConfiguration\s+delegate:self\s+delegateQueue:delegateQueue'
Assert-Contains -Label 'Ve streaming response delegate' -Text $veClientText -Pattern '<NSURLSessionDataDelegate>'
Assert-Contains -Label 'Ve response header callback' -Text $veClientText -Pattern 'didReceiveResponse:'
Assert-Contains -Label 'Ve response body cancellation' -Text $veClientText -Pattern 'completionHandler\(NSURLSessionResponseCancel\)'
Assert-Contains -Label 'Ve task completion cleanup' -Text $veClientText -Pattern 'didCompleteWithError:'
Assert-Contains -Label 'Ve nonaggregating data task' -Text $veClientText -Pattern 'dataTaskWithRequest:request\]'
Assert-NotContains -Label 'Ve aggregating completion-handler task' -Text $veClientText -Pattern 'dataTaskWithRequest:[^\r\n]*completionHandler'
Assert-NotContains -Label 'Ve response data callback forbidden' -Text $veClientText -Pattern 'didReceiveData:'
Assert-Contains -Label 'Ve shared session property' -Text $veClientText -Pattern '@property\s*\(nonatomic,\s*strong\)\s*NSURLSession\s*\*session'
Assert-Contains -Label 'Ve shared session initialization' -Text $veClientText -Pattern '_session\s*=\s*\[NSURLSession sessionWithConfiguration:sessionConfiguration\s+delegate:self\s+delegateQueue:delegateQueue\]'
Assert-Contains -Label 'Ve request timeout' -Text $veClientText -Pattern 'sessionConfiguration\.timeoutIntervalForRequest\s*=\s*kSMSRelayRequestTimeoutSeconds'
Assert-Contains -Label 'Ve resource timeout' -Text $veClientText -Pattern 'sessionConfiguration\.timeoutIntervalForResource\s*=\s*kSMSRelayResourceTimeoutSeconds'
Assert-Contains -Label 'Ve in-flight limit constant' -Text $veClientText -Pattern 'kSMSRelayMaxInFlightTasks\s*=\s*4'
Assert-Contains -Label 'Ve in-flight reservation' -Text $veClientText -Pattern 'activeTaskCount\s*>=\s*kSMSRelayMaxInFlightTasks'
Assert-Contains -Label 'Ve in-flight increment' -Text $veClientText -Pattern 'activeTaskCount\+\+'
Assert-Contains -Label 'Ve in-flight release' -Text $veClientText -Pattern 'activeTaskCount--'
Assert-Contains -Label 'Ve busy fail-closed gate' -Text $veClientText -Pattern '!\[self reserveTaskSlot\]'
Assert-Contains -Label 'Ve shared session task creation' -Text $veClientText -Pattern '\[self\.session dataTaskWithRequest:request\]'
Assert-Contains -Label 'Ve completion releases task slot' -Text $veClientText -Pattern '(?s)didCompleteWithError:.*?\[self releaseTaskSlot\]'
Assert-NotContains -Label 'Ve per-message session invalidation forbidden' -Text $veClientText -Pattern 'finishTasksAndInvalidate|invalidateAndCancel'
$veSessionCreationCount = [regex]::Matches($veClientText, 'sessionWithConfiguration:sessionConfiguration\s+delegate:self').Count
if ($veSessionCreationCount -ne 1) {
    throw "Ve client must create exactly one shared session; count=$veSessionCreationCount"
}
Assert-Contains -Label 'Ve numeric HTTP status log' -Text $veClientText -Pattern 'relay_http_status=%\{public\}ld'
Assert-NotContains -Label 'Ve NSLog forbidden' -Text ($veClientText + $veHookText) -Pattern '\bNSLog\s*\('
Assert-NotContains -Label 'Ve object interpolation forbidden' -Text ($veClientText + $veHookText) -Pattern '%@'
Assert-NotContains -Label 'Ve error description forbidden' -Text ($veClientText + $veHookText) -Pattern 'localizedDescription|debugDescription'
foreach ($line in ($veClientText -split "`n")) {
    if ($line -match '\bos_log_(?:error|info)\s*\(' -and
        $line -notmatch '^\s*os_log_(?:error|info)\(SMSRelayLog\(\),\s*"(?:relay_[a-z_]+|relay_http_status=%\{public\}ld)"(?:,\s*\(long\)statusCode)?\);\s*$') {
        throw "Ve log line is not fixed/redacted: $line"
    }
}

Assert-Contains -Label 'Ve Logos hook class' -Text $veHookText -Pattern '%hook\s+BBServer'
Assert-Contains -Label 'Ve bulletin selector' -Text $veHookText -Pattern 'publishBulletin:\s*\(BBBulletin\s*\*\)bulletin\s+destinations:'
Assert-Contains -Label 'Ve exact MobileSMS filter' -Text $veHookText -Pattern '\[sectionID isEqualToString:@"com\.apple\.MobileSMS"\]'
foreach ($selectorName in @('sectionID', 'title', 'message', 'date')) {
    Assert-Contains -Label "Ve bulletin selector guard $selectorName" -Text $veHookText -Pattern ([regex]::Escape("[bulletin respondsToSelector:@selector($selectorName)]"))
}
Assert-Contains -Label 'Ve bulletin section raw field' -Text $veHookText -Pattern 'id\s+sectionValue\s*=\s*bulletin\.sectionID'
Assert-Contains -Label 'Ve bulletin title raw field' -Text $veHookText -Pattern 'id\s+senderValue\s*=\s*bulletin\.title'
Assert-Contains -Label 'Ve bulletin message raw field' -Text $veHookText -Pattern 'id\s+bodyValue\s*=\s*bulletin\.message'
Assert-Contains -Label 'Ve bulletin date raw field' -Text $veHookText -Pattern 'id\s+dateValue\s*=\s*bulletin\.date'
Assert-Contains -Label 'Ve bulletin section type guard' -Text $veHookText -Pattern '!\[sectionValue isKindOfClass:\[NSString class\]\]'
Assert-Contains -Label 'Ve bulletin sender type guard' -Text $veHookText -Pattern '!\[senderValue isKindOfClass:\[NSString class\]\]'
Assert-Contains -Label 'Ve bulletin body type guard' -Text $veHookText -Pattern '!\[bodyValue isKindOfClass:\[NSString class\]\]'
Assert-Contains -Label 'Ve bulletin date type guard' -Text $veHookText -Pattern '!\[dateValue isKindOfClass:\[NSDate class\]\]'
Assert-Contains -Label 'Ve bulletin section typed field' -Text $veHookText -Pattern 'NSString\s*\*sectionID\s*=\s*\(NSString\s*\*\)sectionValue'
Assert-Contains -Label 'Ve bulletin sender typed field' -Text $veHookText -Pattern 'NSString\s*\*sender\s*=\s*\(NSString\s*\*\)senderValue'
Assert-Contains -Label 'Ve bulletin body typed field' -Text $veHookText -Pattern 'NSString\s*\*body\s*=\s*\(NSString\s*\*\)bodyValue'
Assert-Contains -Label 'Ve bulletin date typed field' -Text $veHookText -Pattern 'NSDate\s*\*date\s*=\s*\(NSDate\s*\*\)dateValue'
Assert-Contains -Label 'Ve hook delegates to client' -Text $veHookText -Pattern '\[\[SMSRelayClient sharedClient\]\s+relayMessageFromSender:sender\s+body:body\s+date:date\]'
Assert-NotContains -Label 'Ve hook signing isolation' -Text $veHookText -Pattern 'CCHmac|CC_SHA256|SecRandomCopyBytes|X-Signature'
$veFilterIndex = $veHookText.IndexOf('[sectionID isEqualToString:@"com.apple.MobileSMS"]', [StringComparison]::Ordinal)
$veDelegateIndex = $veHookText.IndexOf('[[SMSRelayClient sharedClient] relayMessageFromSender:sender body:body date:date]', [StringComparison]::Ordinal)
if ($veFilterIndex -lt 0 -or $veDelegateIndex -lt 0 -or $veFilterIndex -gt $veDelegateIndex) {
    throw 'Ve MobileSMS filter must execute before relay delegation'
}
foreach ($selectorName in @('sectionID', 'title', 'message', 'date')) {
    $guardIndex = $veHookText.IndexOf("[bulletin respondsToSelector:@selector($selectorName)]", [StringComparison]::Ordinal)
    $getterIndex = $veHookText.IndexOf("bulletin.$selectorName", [StringComparison]::Ordinal)
    if ($guardIndex -lt 0 -or $getterIndex -lt 0 -or $guardIndex -gt $getterIndex) {
        throw "Ve selector guard must precede getter access: $selectorName"
    }
}
$veTypeGuardIndex = $veHookText.IndexOf('![sectionValue isKindOfClass:[NSString class]]', [StringComparison]::Ordinal)
if ($veTypeGuardIndex -lt 0 -or $veTypeGuardIndex -gt $veFilterIndex -or $veTypeGuardIndex -gt $veDelegateIndex) {
    throw 'Ve private getter type guards must execute before filter and delegation'
}

Assert-Contains -Label 'Ve Makefile architectures' -Text $veMakefileText -Pattern '(?m)^ARCHS\s*=\s*arm64\s+arm64e\s*$'
Assert-Contains -Label 'Ve Makefile SpringBoard target' -Text $veMakefileText -Pattern '(?m)^INSTALL_TARGET_PROCESSES\s*=\s*SpringBoard\s*$'
Assert-Contains -Label 'Ve Makefile source files' -Text $veMakefileText -Pattern '(?m)^VeSMSRelay_FILES\s*=\s*VeSMSRelay\.xm\s+SMSRelayClient\.m\s*$'
Assert-Contains -Label 'Ve Makefile ARC' -Text $veMakefileText -Pattern '(?m)^VeSMSRelay_CFLAGS\s*=\s*-fobjc-arc\s*$'
Assert-Contains -Label 'Ve Makefile frameworks' -Text $veMakefileText -Pattern '(?m)^VeSMSRelay_FRAMEWORKS\s*=\s*Foundation\s+Security\s*$'
Assert-Contains -Label 'Ve Makefile Theos common' -Text $veMakefileText -Pattern 'include\s+\$\(THEOS\)/makefiles/common\.mk'
Assert-Contains -Label 'Ve Makefile tweak rules' -Text $veMakefileText -Pattern 'include\s+\$\(THEOS_MAKE_PATH\)/tweak\.mk'

Assert-Contains -Label 'Ve README preference path' -Text $veReadmeText -Pattern '/var/mobile/Library/Preferences/com\.smsrelay\.ve\.plist'
Assert-Contains -Label 'Ve README disabled schema' -Text $veReadmeText -Pattern '<key>Enabled</key>\s*<false\s*/>'
Assert-Contains -Label 'Ve README empty credentials' -Text $veReadmeText -Pattern '<key>HMACSecret</key>\s*<string></string>'
Assert-Contains -Label 'Ve README private permissions' -Text $veReadmeText -Pattern 'chmod\s+0600'
Assert-Contains -Label 'Ve README build flow' -Text $veReadmeText -Pattern '(?i)make clean package|make package'
Assert-Contains -Label 'Ve README install flow' -Text $veReadmeText -Pattern '(?i)make package install|install'
Assert-Contains -Label 'Ve README SpringBoard restart' -Text $veReadmeText -Pattern '(?i)respring|restart SpringBoard'
Assert-Contains -Label 'Ve README credential rotation' -Text $veReadmeText -Pattern '(?i)rotation|rotate'
Assert-Contains -Label 'Ve README credential revoke' -Text $veReadmeText -Pattern '(?i)revoke|revocation'
Assert-Contains -Label 'Ve README credential recovery' -Text $veReadmeText -Pattern '(?i)recover|recovery'
Assert-Contains -Label 'Ve README redacted logging' -Text $veReadmeText -Pattern '(?i)redacted|status-only'
Assert-Contains -Label 'Ve README external acceptance' -Text $veReadmeText -Pattern '(?i)external acceptance'
Assert-Contains -Label 'Ve README real iPhone boundary' -Text $veReadmeText -Pattern '(?i)real iPhone'
Assert-Contains -Label 'Ve README injection boundary' -Text $veReadmeText -Pattern '(?i)injection|selector'
Assert-Contains -Label 'Ve README network boundary' -Text $veReadmeText -Pattern '(?i)real network'
Assert-Contains -Label 'Ve README integrated baseline' -Text $veReadmeText -Pattern 'a69204defc3e7fb6b14638f32dc8225807655aa9'
Assert-Contains -Label 'Ve README exclusive deployment warning' -Text $veReadmeText -Pattern '(?i)never both|do not install both|mutually exclusive'
Assert-Contains -Label 'Ve README full capture privacy warning' -Text $veReadmeText -Pattern '(?i)full API|unredacted'

Assert-Contains -Label 'Ve integration target commit' -Text $veIntegrationReadmeText -Pattern 'a69204defc3e7fb6b14638f32dc8225807655aa9'
Assert-Contains -Label 'Ve integration GPL notice' -Text $veIntegrationReadmeText -Pattern '(?i)GPL-3\.0'
Assert-Contains -Label 'Ve integration exact apply command' -Text $veIntegrationReadmeText -Pattern 'git apply'
Assert-Contains -Label 'Ve integration copy client header' -Text $veIntegrationReadmeText -Pattern 'SMSRelayClient\.h'
Assert-Contains -Label 'Ve integration copy client source' -Text $veIntegrationReadmeText -Pattern 'SMSRelayClient\.m'
Assert-Contains -Label 'Ve integration no double hook' -Text $veIntegrationReadmeText -Pattern '(?i)never both|double-process'
Assert-Contains -Label 'Ve patch client import' -Text $veIntegrationPatchText -Pattern '#import\s+"\.\./\.\./Manager/SMSRelayClient\.h"'
Assert-Contains -Label 'Ve patch Security framework' -Text $veIntegrationPatchText -Pattern 'VeCore_FRAMEWORKS\s*=\s*UIKit\s+Security'
Assert-Contains -Label 'Ve patch independent switch' -Text $veIntegrationPatchText -Pattern '!pfEnabled\s*&&\s*!smsRelayEnabled'
Assert-Contains -Label 'Ve patch MobileSMS filter' -Text $veIntegrationPatchText -Pattern 'com\.apple\.MobileSMS'
Assert-Contains -Label 'Ve patch selector guard' -Text $veIntegrationPatchText -Pattern 'respondsToSelector:@selector\(publicationDate\)'
Assert-Contains -Label 'Ve patch date fallback' -Text $veIntegrationPatchText -Pattern 'notificationDate == nil.*respondsToSelector:@selector\(date\)'

Write-Output "PASS ve_source_contract files=$($veRelativeFiles.Count) source=ve_tweak validation=static"
Write-Output "PASS adapter entrypoints present; vectors=$vectorCount"
