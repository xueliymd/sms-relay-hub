package io.smsrelay.collector

import java.security.MessageDigest
import java.time.Instant

data class SmsRelayPayload(
    val schemaVersion: Int,
    val clientMessageId: String,
    val receivedAt: String,
    val sender: String,
    val body: String,
    val source: String = "android_app",
    val simSlot: Int? = null,
    val metadata: Map<String, String> = mapOf(
        "collector_version" to BuildConfig.VERSION_NAME,
        "collector_mode" to "android_app",
    ),
) {
    val idempotencyKey: String get() = clientMessageId

    fun toJson(): String = buildString {
        append('{')
        append("\"schema_version\":").append(schemaVersion)
        append(",\"client_message_id\":").append(jsonString(clientMessageId))
        append(",\"received_at\":").append(jsonString(receivedAt))
        append(",\"sender\":").append(jsonString(sender))
        append(",\"body\":").append(jsonString(body))
        append(",\"source\":").append(jsonString(source))
        if (simSlot != null) append(",\"sim_slot\":").append(simSlot)
        append(",\"metadata\":{")
        normalizedMetadata(metadata).entries.sortedBy { it.key }.forEachIndexed { index, entry ->
            if (index > 0) append(',')
            append(jsonString(entry.key)).append(':').append(jsonString(entry.value))
        }
        append("}}")
    }

    companion object {
        fun fromSms(
            sender: String,
            body: String,
            messageDateMillis: Long,
            receivedAtMillis: Long,
            simSlot: Int? = null,
            subscriptionSnapshot: SubscriptionSnapshot? = null,
            collectorInstanceId: String? = null,
            collectorVersion: String = BuildConfig.VERSION_NAME,
            collectorMode: String = "android_app",
        ): SmsRelayPayload {
            require(sender.isNotBlank()) { "sender is required" }
            require(body.isNotBlank()) { "body is required" }
            val stableInput = "android-app-v1\n$sender\n$messageDateMillis\n$body"
            val digest = MessageDigest.getInstance("SHA-256")
                .digest(stableInput.toByteArray(Charsets.UTF_8))
                .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
            return SmsRelayPayload(
                schemaVersion = 1,
                clientMessageId = "android-app-v1:$digest",
                receivedAt = Instant.ofEpochMilli(receivedAtMillis).toString(),
                sender = sender,
                body = body,
                simSlot = subscriptionSnapshot?.slotIndex ?: simSlot,
                metadata = metadataFor(
                    subscriptionSnapshot = subscriptionSnapshot,
                    collectorInstanceId = collectorInstanceId,
                    collectorVersion = collectorVersion,
                    collectorMode = collectorMode,
                ),
            )
        }

        fun metadataFor(
            subscriptionSnapshot: SubscriptionSnapshot?,
            collectorInstanceId: String? = null,
            collectorVersion: String = BuildConfig.VERSION_NAME,
            collectorMode: String = "android_app",
        ): Map<String, String> =
            normalizedMetadata(
                mapOf(
                    "collector_instance_id" to collectorInstanceId.orEmpty(),
                    "collector_version" to collectorVersion,
                    "collector_mode" to collectorMode,
                ) +
                    subscriptionSnapshot?.toMetadata().orEmpty(),
            )
    }
}

private fun normalizedMetadata(metadata: Map<String, String>): Map<String, String> = buildMap {
    listOf(
        "collector_instance_id",
        "collector_version",
        "collector_mode",
        "subscription_id",
        "carrier_name",
        "phone_number",
    ).forEach { key ->
        metadata[key]?.trim()?.takeIf { it.isNotEmpty() }?.let { put(key, it) }
    }
}

private fun jsonString(value: String): String = buildString {
    append('"')
    value.forEach { char ->
        when (char) {
            '\\' -> append("\\\\")
            '"' -> append("\\\"")
            '\b' -> append("\\b")
            '\u000C' -> append("\\f")
            '\n' -> append("\\n")
            '\r' -> append("\\r")
            '\t' -> append("\\t")
            else -> if (char.code < 0x20) {
                append("\\u").append(char.code.toString(16).padStart(4, '0'))
            } else {
                append(char)
            }
        }
    }
    append('"')
}
