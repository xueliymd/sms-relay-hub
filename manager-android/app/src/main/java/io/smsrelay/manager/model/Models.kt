package io.smsrelay.manager.model

data class PlatformProfile(
    val id: String,
    val label: String,
    val baseUrl: String,
    val username: String,
    val rememberPassword: Boolean,
)

data class AdminSession(
    val token: String,
    val username: String,
    val expiresAt: String,
)

data class AdminDevice(
    val id: String,
    val name: String,
    val clientType: String,
    val active: Boolean,
    val online: Boolean,
    val lastSeenAt: String?,
    val clientInstanceId: String?,
    val clientVersion: String?,
    val clientMode: String?,
    val credentialReuseWarning: Boolean,
    val duplicateCollectionWarning: Boolean,
)

data class SimProfile(
    val deviceId: String,
    val slotIndex: Int,
    val subscriptionId: Int?,
    val phoneNumberMasked: String?,
    val carrierName: String?,
    val source: String,
    val updatedAt: String,
)

/** 远程控制指令（后端 /admin/devices/{id}/commands）。 */
data class DeviceCommand(
    val id: String,
    val deviceId: String,
    val commandType: String,
    val status: String,
    /** 设备回传结果的可读摘要；无结果时为 null。 */
    val resultSummary: String?,
    /** 设备回传结果原文（compact JSON）；无结果时为 null。 */
    val resultJson: String?,
    val createdAt: String,
    val sentAt: String?,
    val completedAt: String?,
) {
    /** 设备端未完成指令的两种状态；超时后后端会返回 expired。 */
    val isPending: Boolean get() = status == "pending" || status == "sent"
    val isCompleted: Boolean get() = status == "completed"
    val isExpired: Boolean get() = status == "expired"
}

/** 指令类型的中文名，与后端 store.DEVICE_COMMAND_TYPES 的说明一一对应。 */
fun commandTypeLabel(value: String): String = when (value) {
    "query_battery" -> "查询电量"
    "query_recent_sms" -> "查最近短信"
    "trigger_upload" -> "触发上报"
    else -> value
}

data class AdminMessage(
    val id: String,
    val deviceId: String,
    val receivedAt: String,
    val createdAt: String,
    val sender: String,
    val body: String?,
    val source: String,
    val simSlot: Int?,
    val subscriptionId: Int?,
    val receivingPhoneMasked: String?,
    val receivingIdentityStatus: String,
    val otpCode: String?,
    val messageDate: String? = null,
    val readAt: String? = null,
)

data class RuntimeInfo(
    val databaseBackend: String,
    val databaseFingerprint: String,
    val platformVersion: String,
    val publicBaseUrl: String,
    val relaySite: String,
    val httpPort: Int,
    val httpsPort: Int,
    val appPort: Int,
    val httpsEnabled: Boolean,
    val services: Map<String, String>,
    val serverTime: String,
)

data class ReleaseEntry(
    val platform: String,
    val channel: String,
    val versionCode: Int,
    val versionName: String,
    val publishedAt: String,
    val notes: String,
    val sizeBytes: Long,
    val sha256: String,
    val downloadUrl: String,
)

enum class ConnectionState {
    DISCONNECTED,
    CONNECTING,
    CONNECTED,
    RECOVERING,
    AUTHENTICATION_FAILED,
    ERROR,
}
