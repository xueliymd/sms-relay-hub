package io.smsrelay.manager.time

import java.time.Instant
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter

object MessageTime {
    private val displayFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")

    fun format(
        value: String?,
        zoneId: ZoneId = ZoneId.systemDefault(),
    ): String {
        val rawValue = value?.trim().orEmpty()
        val instant = parse(rawValue, zoneId)
        return when {
            instant != null -> instant.atZone(zoneId).format(displayFormatter)
            rawValue.isNotEmpty() -> rawValue
            else -> "未知时间"
        }
    }

    fun parse(
        value: String?,
        zoneId: ZoneId = ZoneId.systemDefault(),
    ): Instant? {
        val rawValue = value?.trim().orEmpty()
        if (rawValue.isEmpty()) return null

        epochValue(rawValue)?.let { return it }

        val candidate = if (rawValue.length > 10 && rawValue[10] == ' ') {
            rawValue.replaceRange(10, 11, "T")
        } else {
            rawValue
        }

        return runCatching { OffsetDateTime.parse(candidate).toInstant() }
            .recoverCatching { LocalDateTime.parse(candidate).atZone(zoneId).toInstant() }
            .getOrNull()
    }

    private fun epochValue(value: String): Instant? {
        val timestamp = value.toLongOrNull() ?: return null
        return if (timestamp >= 100_000_000_000 || timestamp <= -100_000_000_000) {
            Instant.ofEpochMilli(timestamp)
        } else {
            Instant.ofEpochSecond(timestamp)
        }
    }
}
