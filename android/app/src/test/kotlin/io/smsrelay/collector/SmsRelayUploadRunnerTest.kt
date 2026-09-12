package io.smsrelay.collector

import kotlin.test.Test
import kotlin.test.assertEquals

class SmsRelayUploadRunnerTest {
    @Test
    fun elapsedMillisUsesMonotonicNanosAndNeverReturnsNegative() {
        assertEquals(12L, UploadTiming.elapsedMillis(1_000_000L, 13_000_000L))
        assertEquals(0L, UploadTiming.elapsedMillis(13_000_000L, 1_000_000L))
    }

    @Test
    fun result_has_explicit_retry_path() {
        assertEquals(SmsRelayUploadRunner.RunResult.Retryable.name, "Retryable")
        assertEquals(SmsRelayUploadRunner.RunResult.Success.name, "Success")
    }
}
