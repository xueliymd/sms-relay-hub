$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$mainRoot = Join-Path $root "app/src/main"
$receiverPath = Join-Path $mainRoot "java/io/smsrelay/collector/SmsReceivedReceiver.kt"
$runnerPath = Join-Path $mainRoot "java/io/smsrelay/collector/SmsRelayUploadRunner.kt"
$servicePath = Join-Path $mainRoot "java/io/smsrelay/collector/SmsRelayForegroundService.kt"
$schedulerPath = Join-Path $mainRoot "java/io/smsrelay/collector/UploadScheduler.kt"
$applicationPath = Join-Path $mainRoot "java/io/smsrelay/collector/RelayApplication.kt"

function Require-Match([string]$path, [string]$pattern, [string]$name) {
    $content = Get-Content -Raw $path
    if ($content -notmatch $pattern) { throw "Missing fast-path contract: $name" }
}

function Reject-Match([string]$path, [string]$pattern, [string]$name) {
    $content = Get-Content -Raw $path
    if ($content -match $pattern) { throw "Invalid fast-path contract: $name" }
}

Require-Match $receiverPath 'ImmediateUploadPayloadStore\.offer\(payload, broadcastReceivedElapsedNanos\)' 'offer captured payload with broadcast timing'
Require-Match $receiverPath 'val broadcastReceivedAtMillis = System\.currentTimeMillis\(\)' 'capture wall-clock time at broadcast entry'
Require-Match $receiverPath 'val broadcastReceivedElapsedNanos = SystemClock\.elapsedRealtimeNanos\(\)' 'capture monotonic time at broadcast entry'
Require-Match $receiverPath 'receivedAtMillis = broadcastReceivedAtMillis' 'platform received_at uses broadcast entry time'
Require-Match $receiverPath 'SmsRelayUploadRunner\.triggerImmediate\(context\)' 'receiver starts the direct upload thread'
Reject-Match $receiverPath 'BackgroundRuntime\.ensureKeepAlive\(context\)' 'receiver must not restart the dataSync foreground service for every SMS'
Reject-Match $receiverPath 'BackgroundRuntime\.requestImmediateUpload\(context\)' 'receiver must not wait for a service upload action'
$receiverContent = Get-Content -Raw $receiverPath
$queuedStatusIndex = $receiverContent.IndexOf('SmsRelayStatusStore(context).record(')
$triggerIndex = $receiverContent.IndexOf('SmsRelayUploadRunner.triggerImmediate(context)')
if ($queuedStatusIndex -lt 0 -or $triggerIndex -lt 0 -or $queuedStatusIndex -gt $triggerIndex) {
    throw 'Invalid fast-path contract: queued status must be recorded before upload starts'
}
Require-Match $runnerPath 'fun runImmediate' 'runner exposes an in-memory fast path'
Require-Match $runnerPath 'ImmediateUploadPayloadStore\.poll\(\)' 'fast path reads the captured payload before queue fallback'
Require-Match $runnerPath 'markUploadedByClientMessageId' 'fast path completes the durable queue row by message id'
Require-Match $servicePath 'ACTION_UPLOAD_NOW -> SmsRelayUploadRunner\.trigger\(this\)' 'service upload action drains the durable queue'
Reject-Match $servicePath 'ACTION_UPLOAD_NOW -> SmsRelayUploadRunner\.triggerImmediate\(this\)' 'service upload action must not only drain in-memory payloads'
Require-Match $schedulerPath 'ExistingWorkPolicy\.REPLACE' 'retry work can be rescheduled after a lost scheduler request'
Require-Match $applicationPath 'SmsRelayRuntime\.initialize\(this\)' 'application initializes reusable runtime state once'

Write-Output "fast path contract passed"
