package io.smsrelay.collector

import kotlin.test.Test
import kotlin.test.assertContains
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse

class SmsRelayProtocolTest {
    @Test
    fun buildsDeterministicPayloadAndIdempotencyKey() {
        val payload = SmsRelayPayload.fromSms(
            sender = "Example",
            body = "Your relay access code is 482731",
            messageDateMillis = 1_754_000_000_000L,
            receivedAtMillis = 1_754_000_005_000L,
            collectorInstanceId = "instance-a",
            collectorVersion = "1.0.11",
        )

        assertEquals("android_app", payload.source)
        assertEquals("android-app-v1:", payload.clientMessageId.substring(0, 15))
        assertEquals(payload.clientMessageId, payload.idempotencyKey)
        assertContains(payload.toJson(), "\"collector_instance_id\":\"instance-a\"")
        assertContains(payload.toJson(), "\"collector_version\":\"1.0.11\"")
        assertContains(payload.toJson(), "\"collector_mode\":\"android_app\"")
        assertEquals(payload.toJson(), payload.toJson())
    }

    @Test
    fun includesAvailableSubscriptionSnapshotMetadata() {
        val payload = SmsRelayPayload.fromSms(
            sender = "Example",
            body = "Your relay access code is 482731",
            messageDateMillis = 1_754_000_000_000L,
            receivedAtMillis = 1_754_000_005_000L,
            subscriptionSnapshot = SubscriptionSnapshot(
                slotIndex = 1,
                subscriptionId = 42,
                carrierName = "Example Carrier",
                phoneNumber = "+15551234567",
            ),
        )

        val json = payload.toJson()
        assertEquals(1, payload.simSlot)
        assertContains(json, "\"collector_version\":\"${BuildConfig.VERSION_NAME}\"")
        assertContains(json, "\"collector_mode\":\"android_app\"")
        assertContains(json, "\"subscription_id\":\"42\"")
        assertContains(json, "\"carrier_name\":\"Example Carrier\"")
        assertContains(json, "\"phone_number\":\"+15551234567\"")
    }

    @Test
    fun resolvedSubscriptionSlotWinsOverConflictingBroadcastSlot() {
        val payload = SmsRelayPayload.fromSms(
            sender = "Example",
            body = "Your relay access code is 482731",
            messageDateMillis = 1_754_000_000_000L,
            receivedAtMillis = 1_754_000_005_000L,
            simSlot = 0,
            subscriptionSnapshot = SubscriptionSnapshot(
                slotIndex = 1,
                subscriptionId = 42,
                carrierName = "SIM2 Carrier",
                phoneNumber = "+15551234567",
            ),
        )

        assertEquals(1, payload.simSlot)
    }

    @Test
    fun omitsUnavailableOrBlankSubscriptionMetadata() {
        val payload = SmsRelayPayload.fromSms(
            sender = "Example",
            body = "Your relay access code is 482731",
            messageDateMillis = 1_754_000_000_000L,
            receivedAtMillis = 1_754_000_005_000L,
            subscriptionSnapshot = SubscriptionSnapshot(
                carrierName = "   ",
                phoneNumber = "",
            ),
        )

        val json = payload.toJson()
        assertContains(json, "\"collector_version\":\"${BuildConfig.VERSION_NAME}\"")
        assertContains(json, "\"collector_mode\":\"android_app\"")
        assertFalse(json.contains("\"sim_slot\""))
        assertFalse(json.contains("subscription_id"))
        assertFalse(json.contains("carrier_name"))
        assertFalse(json.contains("phone_number"))
    }

    @Test
    fun signsCanonicalRequestUsingBase64UrlSecret() {
        val signature = SmsRelaySigner.sign(
            secret = "c2VjcmV0",
            timestamp = "1754000000",
            nonce = "nonce-1",
            body = "{\"body\":\"hello\"}",
        )

        assertEquals("95JQl2oKq8TLn38BttFinYIcAmsP8S7exC-sFEyzg_g", signature)
    }

    @Test
    fun acceptsUnpaddedBase64UrlHmacSecret() {
        assertEquals(
            SmsRelaySigner.sign("c2VjcmV0", "1754000000", "nonce-1", "{}"),
            SmsRelaySigner.sign("c2VjcmV0=", "1754000000", "nonce-1", "{}"),
        )
    }

    @Test
    fun acceptsPrivateHttpBaseUrl() {
        assertEquals("http://192.168.5.7:8000", SmsRelayConfig.validateBaseUrl("http://192.168.5.7:8000/"))
    }
}
