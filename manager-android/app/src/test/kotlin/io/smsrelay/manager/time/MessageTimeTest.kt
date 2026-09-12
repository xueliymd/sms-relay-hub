package io.smsrelay.manager.time

import java.time.ZoneId
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class MessageTimeTest {
    private val shanghai = ZoneId.of("Asia/Shanghai")

    @Test
    fun formatsUtcTimestampInTargetTimeZone() {
        assertEquals("2026-09-05 16:00:01", MessageTime.format("2026-09-05T08:00:01Z", shanghai))
    }

    @Test
    fun formatsTimestampWithExplicitOffset() {
        assertEquals(
            "2026-09-05 16:00:01",
            MessageTime.format("2026-09-05T16:00:01+08:00", shanghai),
        )
    }

    @Test
    fun formatsLocalTimestampWithoutChangingWallClock() {
        assertEquals(
            "2026-09-05 16:00:01",
            MessageTime.format("2026-09-05 16:00:01", shanghai),
        )
    }

    @Test
    fun parsesSecondAndMillisecondEpochValues() {
        assertEquals(
            "2026-09-05 16:00:01",
            MessageTime.format("1788595201", shanghai),
        )
        assertEquals(
            "2026-09-05 16:00:01",
            MessageTime.format("1788595201000", shanghai),
        )
    }

    @Test
    fun fallsBackToRawValueAndReturnsNullForInvalidInput() {
        assertEquals("not-a-time", MessageTime.format("not-a-time", shanghai))
        assertEquals("未知时间", MessageTime.format(" ", shanghai))
        assertNull(MessageTime.parse("not-a-time", shanghai))
    }
}
