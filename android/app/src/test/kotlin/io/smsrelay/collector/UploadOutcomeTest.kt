package io.smsrelay.collector

import kotlin.test.Test
import kotlin.test.assertEquals

class UploadOutcomeTest {
    @Test
    fun classifiesServerResponsesWithoutExposingPayloads() {
        assertEquals(UploadOutcome.Uploaded, UploadOutcome.fromStatus(201))
        assertEquals(UploadOutcome.Uploaded, UploadOutcome.fromStatus(200))
        assertEquals(UploadOutcome.Conflict, UploadOutcome.fromStatus(409))
        assertEquals(UploadOutcome.AuthBlocked, UploadOutcome.fromStatus(401))
        assertEquals(UploadOutcome.AuthBlocked, UploadOutcome.fromStatus(403))
        assertEquals(UploadOutcome.Retryable, UploadOutcome.fromStatus(429))
        assertEquals(UploadOutcome.Retryable, UploadOutcome.fromStatus(503))
        assertEquals(UploadOutcome.PermanentFailure, UploadOutcome.fromStatus(422))
    }
}
