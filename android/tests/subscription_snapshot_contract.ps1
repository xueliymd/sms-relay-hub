$ErrorActionPreference = "Stop"

$androidRoot = Split-Path -Parent $PSScriptRoot
$mainRoot = Join-Path $androidRoot "app/src/main"
$subscriptionPath = Join-Path $mainRoot "java/io/smsrelay/collector/SubscriptionSnapshot.kt"
$hintResolverPath = Join-Path $mainRoot "java/io/smsrelay/collector/SmsSubscriptionHintResolver.kt"
$receiverPath = Join-Path $mainRoot "java/io/smsrelay/collector/SmsReceivedReceiver.kt"
$payloadPath = Join-Path $mainRoot "java/io/smsrelay/collector/SmsRelayPayload.kt"
$databasePath = Join-Path $mainRoot "java/io/smsrelay/collector/SmsRelayDatabase.kt"
$repositoryPath = Join-Path $mainRoot "java/io/smsrelay/collector/SmsRelayQueueRepository.kt"
$activityPath = Join-Path $mainRoot "java/io/smsrelay/collector/MainActivity.kt"
$manifestPath = Join-Path $mainRoot "AndroidManifest.xml"
$readmePath = Join-Path $androidRoot "README.md"

function Require-File([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Missing required file: $Path"
    }
}

# Windows PowerShell 5.1 decodes files as the ANSI codepage by default, which mangles the
# Chinese strings inside the Kotlin sources these contracts match against, so every reader
# is pinned to UTF-8.
function Require-Match([string]$Path, [string]$Pattern, [string]$Description) {
    Require-File $Path
    $content = Get-Content -Raw -Encoding UTF8 -LiteralPath $Path
    if ($content -notmatch $Pattern) {
        throw "Missing contract '$Description' in $Path"
    }
}

function Require-NoMatch([string]$Path, [string]$Pattern, [string]$Description) {
    Require-File $Path
    $content = Get-Content -Raw -Encoding UTF8 -LiteralPath $Path
    if ($content -match $Pattern) {
        throw "Forbidden contract '$Description' in $Path"
    }
}

Require-Match $subscriptionPath 'data class SubscriptionSnapshot' "snapshot value object"
Require-Match $subscriptionPath 'class SubscriptionSnapshotProvider' "testable snapshot provider"
Require-Match $subscriptionPath 'SubscriptionSnapshotSource' "injectable protected-field source"
Require-Match $subscriptionPath 'Manifest\.permission\.READ_PHONE_STATE' "phone-state permission gate"
Require-Match $subscriptionPath 'Manifest\.permission\.READ_PHONE_NUMBERS' "phone-number permission gate"
Require-Match $subscriptionPath 'createForSubscriptionId' "subscription-scoped telephony manager"
Require-Match $subscriptionPath 'line1Number' "optional line1Number lookup"
Require-Match $subscriptionPath 'catch \(_:\s*SecurityException\)' "SecurityException fallback"
Require-Match $subscriptionPath 'catch \(_:\s*RuntimeException\)' "runtime failure fallback"
Require-Match $subscriptionPath 'findSubscriptionById' "subscription ID lookup"
Require-Match $subscriptionPath 'findSubscriptionBySlot' "slot fallback lookup"
Require-Match $subscriptionPath 'SubscriptionLookupResult\.Missing' "proven-missing subscription result"
Require-Match $subscriptionPath 'SubscriptionLookupResult\.Found' "validated subscription result"
Require-Match $subscriptionPath 'subscriptionId\s*=\s*null' "stale subscription hint removal"
Require-Match $subscriptionPath 'subscriptionHintFailed' "failed subscription hint preservation"
Require-Match $subscriptionPath 'SubscriptionLookupResult\.Missing\s*->\s*if\s*\(subscriptionHintFailed\)' "slot miss preserves an unverified subscription hint"
Require-Match $subscriptionPath 'SubscriptionLookupResult\.Failed\s*->\s*phoneLookupAllowed\s*=\s*false' "failed lookup blocks unverified phone-number access"

Require-Match $hintResolverPath 'android\.telephony\.extra\.SLOT_INDEX' "platform slot hint"
Require-Match $hintResolverPath 'android\.telephony\.extra\.SUBSCRIPTION_INDEX' "platform subscription hint"
Require-Match $hintResolverPath '"slot"' "legacy slot hint"
Require-Match $hintResolverPath '"subscription"' "legacy subscription hint"
Require-Match $receiverPath 'SmsRelayRuntime\.captureSubscription' "receiver snapshot provider"
Require-Match $receiverPath 'val subscriptionSnapshot\s*=\s*try' "subscription enrichment is isolated"
Require-Match $receiverPath 'catch \(_:\s*Exception\)\s*\{\s*subscriptionMetadataFailed\s*=\s*true\s*null' "subscription enrichment failure does not drop SMS"
Require-Match $receiverPath 'slotIndexHint\s*=\s*subscriptionHints\.slotIndex' "slot hint passed to provider"
Require-Match $receiverPath 'subscriptionIdHint\s*=\s*subscriptionHints\.subscriptionId' "subscription hint passed to provider"
Require-Match $receiverPath 'simSlot\s*=\s*subscriptionHints\.slotIndex' "broadcast slot survives missing subscription metadata"
Require-Match $receiverPath 'subscriptionSnapshot\s*=\s*subscriptionSnapshot' "snapshot merged into payload"
Require-Match $receiverPath 'SIM元数据暂不可用' "metadata failure is visible without exposing SMS content"
Require-Match $receiverPath 'catch \(_:\s*Exception\)' "outer non-fatal worker exception boundary"
Require-Match $receiverPath 'SmsRelayStatusStore\(context\)\.record\(CAPTURE_FAILURE_STATUS\)' "constant sanitized failure status"
Require-Match $receiverPath 'finally' "pending result completion in finally"
Require-Match $receiverPath 'pendingResult\.finish\(\)' "pending result is finished"
Require-NoMatch $receiverPath 'catch \(_:\s*(Throwable|Error)\)' "fatal VM error swallowing"
Require-NoMatch $receiverPath '(error|exception)\.message' "exception detail in receiver status"

Require-Match $payloadPath '"collector_version"\s+to\s+collectorVersion' "collector version metadata"
Require-Match $payloadPath 'subscriptionSnapshot:\s*SubscriptionSnapshot\?' "optional snapshot payload input"
Require-Match $payloadPath 'subscriptionSnapshot\?\.toMetadata' "snapshot metadata merge"

Require-Match $databasePath 'subscription_id' "queued subscription id"
Require-Match $databasePath 'carrier_name' "queued carrier name"
Require-Match $databasePath 'phone_number_ciphertext' "encrypted queued phone number"
Require-Match $databasePath 'Migration\(1,\s*2\)' "Room schema migration"
Require-Match $repositoryPath 'cipher\.seal\(.*phoneNumber' "phone number encrypted at rest"
Require-Match $repositoryPath 'cipher\.open\(.*phoneNumberCiphertext' "phone number restored for upload"
Require-Match $databasePath 'phone_number_ciphertext\s*=\s*NULL' "terminal queue phone-number redaction"

Require-Match $manifestPath 'android\.permission\.RECEIVE_SMS' "required SMS permission"
Require-Match $manifestPath 'android\.permission\.READ_PHONE_STATE' "optional phone-state declaration"
Require-Match $manifestPath 'android\.permission\.READ_PHONE_NUMBERS' "optional phone-number declaration"
Require-Match $activityPath 'OPTIONAL_PHONE_METADATA_REQUEST' "separate optional permission request"
Require-Match $activityPath 'optionalPermissionStatus' "separate optional permission status"
Require-Match $activityPath 'Manifest\.permission\.RECEIVE_SMS\),\s*RECEIVE_SMS_REQUEST' "isolated required permission request"

Require-Match $readmePath 'MSISDN' "MSISDN availability caveat"
Require-Match $readmePath 'manual' "manual SIM profile mapping fallback"
Require-Match $readmePath 'optional permissions' "optional permission rejection boundary"

Require-NoMatch $receiverPath 'SmsRelayStatusStore[^\r\n]*(phoneNumber|carrierName|subscriptionId|subscriptionSnapshot)' "sensitive receiver status text"
Require-NoMatch $activityPath 'Toast[^\r\n]*(phoneNumber|carrierName|subscriptionId|subscriptionSnapshot)' "sensitive UI toast text"
Require-NoMatch $subscriptionPath '(Log\.|println|printStackTrace)' "subscription metadata logging"

Write-Output "PASS: Android subscription metadata source contract"
