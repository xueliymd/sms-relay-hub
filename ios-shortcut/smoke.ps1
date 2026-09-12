$ErrorActionPreference = 'Stop'

$missing = @('SMS_RELAY_BASE_URL', 'SMS_RELAY_DEVICE_TOKEN') | Where-Object {
    -not [Environment]::GetEnvironmentVariable($_)
}
if ($missing.Count -gt 0) {
    throw "Missing required environment variables: $($missing -join ', ')"
}

$baseUrl = $env:SMS_RELAY_BASE_URL.TrimEnd('/')
if (-not $baseUrl.StartsWith('http://', [StringComparison]::OrdinalIgnoreCase) -and -not $baseUrl.StartsWith('https://', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'SMS_RELAY_BASE_URL must use HTTP or HTTPS'
}

$message = [ordered]@{
    Sender = 'Authorized Relay Test'
    Content = 'Your relay access code is 482731'
    Date = '2026-08-21T12:40:00+08:00'
}

$client = [Net.Http.HttpClient]::new()
$client.DefaultRequestHeaders.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $env:SMS_RELAY_DEVICE_TOKEN)

function Send-ShortcutRequest($value) {
    $json = @{ message = $value } | ConvertTo-Json -Depth 8 -Compress
    $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Post, "$baseUrl/api/v1/ingest/shortcut")
    $request.Content = [Net.Http.StringContent]::new($json, [Text.Encoding]::UTF8, 'application/json')
    try {
        $response = $client.Send($request)
        $content = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        return @{ Status = [int]$response.StatusCode; Data = ($content | ConvertFrom-Json) }
    } finally {
        $request.Dispose()
    }
}

try {
    $first = Send-ShortcutRequest $message
    $second = Send-ShortcutRequest $message
    $changed = $message.Clone()
    $changed.Content = 'Your relay access code is 927415'
    $third = Send-ShortcutRequest $changed

    if ($first.Status -ne 201) { throw "Expected first=201, got $($first.Status)" }
    if ($second.Status -ne 200 -or -not $second.Data.duplicate) { throw 'Expected duplicate=200' }
    if ($third.Status -ne 201) { throw "Expected changed=201, got $($third.Status)" }
    Write-Output 'first=201 duplicate=false'
    Write-Output 'second=200 duplicate=true'
    Write-Output 'changed=201 duplicate=false'
} finally {
    $client.Dispose()
}
