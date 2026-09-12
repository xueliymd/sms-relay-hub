package io.smsrelay.collector

import kotlin.test.Test
import kotlin.test.assertEquals

class SubscriptionSnapshotCacheTest {
    @Test
    fun repeatedSmsForTheSameSubscriptionUsesOneSystemLookup() {
        var lookups = 0
        val expected = SubscriptionSnapshot(
            slotIndex = 0,
            subscriptionId = 7,
            carrierName = "Carrier",
            phoneNumber = "+8613800138000",
        )
        val cache = SubscriptionSnapshotCache { _, _ ->
            lookups += 1
            expected
        }

        assertEquals(expected, cache.capture(slotIndexHint = 0, subscriptionIdHint = 7))
        assertEquals(expected, cache.capture(slotIndexHint = 0, subscriptionIdHint = 7))
        assertEquals(1, lookups)
    }

    @Test
    fun aDifferentSubscriptionGetsItsOwnSnapshot() {
        var lookups = 0
        val cache = SubscriptionSnapshotCache { slot, subscription ->
            lookups += 1
            SubscriptionSnapshot(slotIndex = slot, subscriptionId = subscription)
        }

        cache.capture(slotIndexHint = 0, subscriptionIdHint = 7)
        cache.capture(slotIndexHint = 1, subscriptionIdHint = 8)

        assertEquals(2, lookups)
    }

    @Test
    fun invalidationForcesOneFreshSystemLookup() {
        var lookups = 0
        val cache = SubscriptionSnapshotCache { slot, subscription ->
            lookups += 1
            SubscriptionSnapshot(slotIndex = slot, subscriptionId = subscription)
        }

        cache.capture(slotIndexHint = 0, subscriptionIdHint = 7)
        cache.clear()
        cache.capture(slotIndexHint = 0, subscriptionIdHint = 7)

        assertEquals(2, lookups)
    }

    @Test
    fun slotOnlyHintsAreNotCachedAcrossMessages() {
        var lookups = 0
        val cache = SubscriptionSnapshotCache { _, _ ->
            lookups += 1
            SubscriptionSnapshot(slotIndex = lookups - 1, subscriptionId = lookups)
        }

        cache.capture(slotIndexHint = 1, subscriptionIdHint = null)
        cache.capture(slotIndexHint = 1, subscriptionIdHint = null)

        assertEquals(2, lookups)
    }

    @Test
    fun missingBroadcastHintsAreNotCachedAcrossMessages() {
        var lookups = 0
        val cache = SubscriptionSnapshotCache { _, _ ->
            lookups += 1
            SubscriptionSnapshot(slotIndex = null, subscriptionId = null)
        }

        cache.capture(slotIndexHint = null, subscriptionIdHint = null)
        cache.capture(slotIndexHint = null, subscriptionIdHint = null)

        assertEquals(2, lookups)
    }
}
