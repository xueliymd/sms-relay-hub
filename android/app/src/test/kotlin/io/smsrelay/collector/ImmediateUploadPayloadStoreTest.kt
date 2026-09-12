package io.smsrelay.collector

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class ImmediateUploadPayloadStoreTest {
    @Test
    fun offeredPayloadIsConsumedExactlyOnce() {
        val payload = SmsRelayPayload(
            schemaVersion = 1,
            clientMessageId = "fast-path-1",
            receivedAt = "2026-08-23T00:00:00Z",
            sender = "1069",
            body = "verification code 123456",
        )

        ImmediateUploadPayloadStore.offer(payload, broadcastReceivedElapsedNanos = 123_000_000L)

        val item = ImmediateUploadPayloadStore.poll()
        assertEquals(payload, item?.payload)
        assertEquals(123_000_000L, item?.broadcastReceivedElapsedNanos)
        assertNull(ImmediateUploadPayloadStore.poll())
    }

    @Test
    fun severalUnconsumedPayloadsRemainAvailableByMessageId() {
        val first = SmsRelayPayload(1, "first", "2026-08-23T00:00:00Z", "1069", "111111")
        val second = SmsRelayPayload(1, "second", "2026-08-23T00:00:01Z", "1069", "222222")

        ImmediateUploadPayloadStore.offer(first, broadcastReceivedElapsedNanos = 100L)
        ImmediateUploadPayloadStore.offer(second, broadcastReceivedElapsedNanos = 200L)

        assertEquals(first, ImmediateUploadPayloadStore.poll()?.payload)
        assertEquals(second, ImmediateUploadPayloadStore.poll()?.payload)
        assertNull(ImmediateUploadPayloadStore.poll())
    }
}
