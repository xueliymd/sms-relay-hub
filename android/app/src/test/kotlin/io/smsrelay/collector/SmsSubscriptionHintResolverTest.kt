package io.smsrelay.collector

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class SmsSubscriptionHintResolverTest {
    @Test
    fun fallsBackToMessageSubscriptionIdWhenBroadcastExtrasAreMissing() {
        val hints = SmsSubscriptionHintResolver.resolve(
            extras = emptyMap(),
            messageSubscriptionIds = listOf(null, 42),
        )

        assertNull(hints.slotIndex)
        assertEquals(42, hints.subscriptionId)
    }

    @Test
    fun readsOemStringAndArrayExtras() {
        val hints = SmsSubscriptionHintResolver.resolve(
            extras = mapOf(
                "slotId" to "1",
                "subscription_id" to intArrayOf(42),
            ),
            messageSubscriptionIds = emptyList(),
        )

        assertEquals(1, hints.slotIndex)
        assertEquals(42, hints.subscriptionId)
    }

    @Test
    fun explicitBroadcastSubscriptionWinsOverMessageFallback() {
        val hints = SmsSubscriptionHintResolver.resolve(
            extras = mapOf("android.telephony.extra.SUBSCRIPTION_INDEX" to 7),
            messageSubscriptionIds = listOf(42),
        )

        assertEquals(7, hints.subscriptionId)
    }
}
