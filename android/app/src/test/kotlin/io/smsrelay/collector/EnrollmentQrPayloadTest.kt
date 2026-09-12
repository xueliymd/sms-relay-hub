package io.smsrelay.collector

import java.time.Instant
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class EnrollmentQrPayloadTest {
    private val deviceId = "123e4567-e89b-12d3-a456-426614174000"
    private val now = Instant.parse("2026-09-02T12:00:00Z")

    @Test
    fun parsesSupportedPayload() {
        val payload = EnrollmentQrPayload.parse(
            """{"v":1,"type":"sms-relay-enroll","url":"https://relay.example","device_id":"$deviceId","token":"${"a".repeat(43)}","expires_at":"2026-09-02T12:05:00Z"}""",
            now,
        )

        assertEquals("https://relay.example", payload.baseUrl)
        assertEquals(deviceId, payload.deviceId)
    }

    @Test
    fun rejectsExpiredPayload() {
        assertFailsWith<IllegalArgumentException> {
            EnrollmentQrPayload.parse(
                """{"v":1,"type":"sms-relay-enroll","url":"https://relay.example","device_id":"$deviceId","token":"${"a".repeat(43)}","expires_at":"2026-09-02T11:59:59Z"}""",
                now,
            )
        }
    }

    @Test
    fun rejectsForeignPayloadType() {
        assertFailsWith<IllegalArgumentException> {
            EnrollmentQrPayload.parse(
                """{"v":1,"type":"other-app","url":"https://relay.example","device_id":"$deviceId","token":"${"a".repeat(43)}","expires_at":"2026-09-02T12:05:00Z"}""",
                now,
            )
        }
    }
}
