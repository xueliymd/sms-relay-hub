$ErrorActionPreference = 'Stop'

$required = @('SMS_RELAY_BASE_URL', 'SMS_RELAY_DEVICE_ID', 'SMS_RELAY_HMAC_SECRET')
$missing = $required | Where-Object { -not [Environment]::GetEnvironmentVariable($_) }
if ($missing.Count -gt 0) {
    throw "Missing required environment variables: $($missing -join ', ')"
}

$baseUrl = $env:SMS_RELAY_BASE_URL.TrimEnd('/')
if (-not $baseUrl.StartsWith('http://', [StringComparison]::OrdinalIgnoreCase) -and -not $baseUrl.StartsWith('https://', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'SMS_RELAY_BASE_URL must use HTTP or HTTPS'
}

$clientId = "android-contract-smoke:$([guid]::NewGuid())"
$payload = [ordered]@{
    schema_version = 1
    client_message_id = $clientId
    received_at = [DateTimeOffset]::UtcNow.ToString('o')
    sender = 'Authorized Relay Test'
    body = 'Your relay access code is 482731'
    source = 'android_app'
    metadata = @{ contract_smoke = '1' }
}
$json = $payload | ConvertTo-Json -Depth 8 -Compress
$bodyBytes = [Text.Encoding]::UTF8.GetBytes($json)

function ConvertFrom-Base64Url([string]$value) {
    $normalized = $value.Replace('-', '+').Replace('_', '/')
    $normalized += '=' * ((4 - $normalized.Length % 4) % 4)
    return [Convert]::FromBase64String($normalized)
}

function ConvertTo-Base64Url([byte[]]$value) {
    return [Convert]::ToBase64String($value).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Send-SignedRelayRequest {
    $timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds().ToString()
    $nonce = [guid]::NewGuid().ToString()
    $hashHex = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bodyBytes)).ToLowerInvariant()
    $canonical = [Text.Encoding]::UTF8.GetBytes("$timestamp`n$nonce`n$hashHex")
    $hmac = [Security.Cryptography.HMACSHA256]::new((ConvertFrom-Base64Url $env:SMS_RELAY_HMAC_SECRET))
    try { $signature = ConvertTo-Base64Url ($hmac.ComputeHash($canonical)) } finally { $hmac.Dispose() }

    $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Post, "$baseUrl/api/v1/ingest/messages")
    $request.Headers.Add('X-Device-Id', $env:SMS_RELAY_DEVICE_ID)
    $request.Headers.Add('X-Timestamp', $timestamp)
    $request.Headers.Add('X-Nonce', $nonce)
    $request.Headers.Add('X-Signature', $signature)
    $request.Headers.Add('X-Idempotency-Key', $clientId)
    $request.Content = [Net.Http.ByteArrayContent]::new($bodyBytes)
    $request.Content.Headers.ContentType = [Net.Http.Headers.MediaTypeHeaderValue]::new('application/json')
    try {
        $response = $script:httpClient.Send($request)
        $content = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        return @{ Status = [int]$response.StatusCode; Data = ($content | ConvertFrom-Json) }
    } finally {
        $request.Dispose()
    }
}

$httpClient = [Net.Http.HttpClient]::new()
try {
    $first = Send-SignedRelayRequest
    $second = Send-SignedRelayRequest
    if ($first.Status -ne 201) { throw "Expected first=201, got $($first.Status)" }
    if ($second.Status -ne 200 -or -not $second.Data.duplicate) { throw 'Expected duplicate=200' }
    Write-Output 'first=201 duplicate=false'
    Write-Output 'second=200 duplicate=true'
} finally {
    $httpClient.Dispose()
}
