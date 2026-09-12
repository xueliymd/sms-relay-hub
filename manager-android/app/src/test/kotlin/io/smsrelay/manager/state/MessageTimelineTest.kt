package io.smsrelay.manager.state

import io.smsrelay.manager.model.AdminMessage
import io.smsrelay.manager.time.MessageTime
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class MessageTimelineTest {
    @Test
    fun deduplicatesOrdersNewestFirstCapsAndTracksCursor() {
        val timeline = MessageTimeline(capacity = 500)
        val messages = (0..500).map { index -> message("id-" + index.toString().padStart(3, '0')) }
        timeline.merge(messages + message("id-500", body = "updated"))

        assertEquals(500, timeline.items().size)
        assertEquals("id-500", timeline.items().first().id)
        assertEquals("updated", timeline.items().first().body)
        assertEquals("id-001", timeline.items().last().id)
        assertEquals(MessageCursor("2026-09-01T08:00:00Z", "id-500"), timeline.cursor())
    }

    @Test
    fun ordersByParsedTimestampInsteadOfRawString() {
        val timeline = MessageTimeline()
        timeline.merge(
            listOf(
                message("old-offset", createdAt = "2026-09-01T10:00:00+02:00"),
                message("new-utc", createdAt = "2026-09-01T09:00:00Z"),
                message("invalid", createdAt = "not-a-time"),
            ),
        )

        assertEquals(listOf("new-utc", "old-offset", "invalid"), timeline.items().map { it.id })
        assertEquals(MessageCursor("2026-09-01T09:00:00Z", "new-utc"), timeline.cursor())
    }

    @Test
    fun clearRemovesMessagesAndCursor() {
        val timeline = MessageTimeline()
        timeline.merge(listOf(message("one")))
        timeline.clear()

        assertEquals(emptyList(), timeline.items())
        assertNull(timeline.cursor())
    }

    private fun message(
        id: String,
        body: String = id,
        createdAt: String = "2026-09-01T08:00:00Z",
    ) = AdminMessage(
        id = id,
        deviceId = "device-1",
        receivedAt = "2026-09-01T08:00:00Z",
        createdAt = createdAt,
        sender = "1069",
        body = body,
        source = "android_app",
        simSlot = 0,
        subscriptionId = 42,
        receivingPhoneMasked = "******1234",
        receivingIdentityStatus = "resolved",
        otpCode = null,
    )
}
