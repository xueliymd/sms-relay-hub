package io.smsrelay.collector

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull

class SubscriptionSnapshotTest {
    @Test
    fun capturesAvailableSubscriptionMetadata() {
        val source = FakeSubscriptionSnapshotSource(
            canReadSubscriptionInfo = true,
            canReadPhoneNumber = true,
            recordsBySubscriptionId = mapOf(
                42 to SubscriptionRecord(
                    slotIndex = 1,
                    subscriptionId = 42,
                    carrierName = "Example Carrier",
                ),
            ),
            phoneNumber = "+15551234567",
        )

        val snapshot = SubscriptionSnapshotProvider(source).capture(
            slotIndexHint = 0,
            subscriptionIdHint = 42,
        )

        assertEquals(1, snapshot.slotIndex)
        assertEquals(42, snapshot.subscriptionId)
        assertEquals("Example Carrier", snapshot.carrierName)
        assertEquals("+15551234567", snapshot.phoneNumber)
        assertEquals(
            mapOf(
                "subscription_id" to "42",
                "carrier_name" to "Example Carrier",
                "phone_number" to "+15551234567",
            ),
            snapshot.toMetadata(),
        )
    }

    @Test
    fun fallsBackToSlotWhenSubscriptionHintIsStale() {
        val source = FakeSubscriptionSnapshotSource(
            canReadSubscriptionInfo = true,
            canReadPhoneNumber = false,
            recordsBySlotIndex = mapOf(
                2 to SubscriptionRecord(
                    slotIndex = 2,
                    subscriptionId = 77,
                    carrierName = "Slot Carrier",
                ),
            ),
        )

        val snapshot = SubscriptionSnapshotProvider(source).capture(
            slotIndexHint = 2,
            subscriptionIdHint = 999,
        )

        assertEquals(2, snapshot.slotIndex)
        assertEquals(77, snapshot.subscriptionId)
        assertEquals("Slot Carrier", snapshot.carrierName)
        assertEquals(listOf("subscription:999", "slot:2"), source.subscriptionLookups)
        assertFalse(snapshot.toMetadata().containsValue("999"))
    }

    @Test
    fun matchedSubscriptionRecordWinsOverConflictingSlotHint() {
        val source = FakeSubscriptionSnapshotSource(
            canReadSubscriptionInfo = true,
            canReadPhoneNumber = false,
            recordsBySubscriptionId = mapOf(
                42 to SubscriptionRecord(
                    slotIndex = 1,
                    subscriptionId = 42,
                    carrierName = "Matched Carrier",
                ),
            ),
            recordsBySlotIndex = mapOf(
                0 to SubscriptionRecord(
                    slotIndex = 0,
                    subscriptionId = 7,
                    carrierName = "Wrong Slot Carrier",
                ),
            ),
        )

        val snapshot = SubscriptionSnapshotProvider(source).capture(
            slotIndexHint = 0,
            subscriptionIdHint = 42,
        )

        assertEquals(1, snapshot.slotIndex)
        assertEquals(42, snapshot.subscriptionId)
        assertEquals("Matched Carrier", snapshot.carrierName)
        assertEquals(listOf("subscription:42"), source.subscriptionLookups)
    }

    @Test
    fun omitsSubscriptionHintProvenMissingByProtectedLookup() {
        val source = FakeSubscriptionSnapshotSource(
            canReadSubscriptionInfo = true,
            canReadPhoneNumber = true,
        )

        val snapshot = SubscriptionSnapshotProvider(source).capture(
            slotIndexHint = 3,
            subscriptionIdHint = 999,
        )

        assertEquals(3, snapshot.slotIndex)
        assertNull(snapshot.subscriptionId)
        assertNull(snapshot.phoneNumber)
        assertFalse(snapshot.toMetadata().containsKey("subscription_id"))
        assertEquals(listOf("subscription:999", "slot:3"), source.subscriptionLookups)
        assertFalse(source.phoneNumberRead)
    }

    @Test
    fun preservesSubscriptionHintWhenIdLookupFailsAndSlotLookupMisses() {
        val source = FakeSubscriptionSnapshotSource(
            canReadSubscriptionInfo = true,
            canReadPhoneNumber = true,
            subscriptionIdFailure = SecurityException("subscription lookup denied"),
            phoneNumber = "+15550000999",
        )

        val snapshot = SubscriptionSnapshotProvider(source).capture(
            slotIndexHint = 3,
            subscriptionIdHint = 999,
        )

        assertEquals(3, snapshot.slotIndex)
        assertEquals(999, snapshot.subscriptionId)
        assertNull(snapshot.carrierName)
        assertNull(snapshot.phoneNumber)
        assertEquals(mapOf("subscription_id" to "999"), snapshot.toMetadata())
        assertEquals(listOf("subscription:999", "slot:3"), source.subscriptionLookups)
        assertFalse(source.phoneNumberRead)
    }

    @Test
    fun skipsProtectedLookupsWithoutOptionalPermissions() {
        val source = FakeSubscriptionSnapshotSource(
            canReadSubscriptionInfo = false,
            canReadPhoneNumber = false,
            failIfRead = true,
        )

        val snapshot = SubscriptionSnapshotProvider(source).capture(
            slotIndexHint = 2,
            subscriptionIdHint = 7,
        )

        assertEquals(2, snapshot.slotIndex)
        assertEquals(7, snapshot.subscriptionId)
        assertNull(snapshot.carrierName)
        assertNull(snapshot.phoneNumber)
        assertEquals(mapOf("subscription_id" to "7"), snapshot.toMetadata())
        assertFalse(source.subscriptionInfoRead)
        assertFalse(source.phoneNumberRead)
    }

    @Test
    fun runtimeFailuresOmitUnavailableValues() {
        val source = FakeSubscriptionSnapshotSource(
            canReadSubscriptionInfo = true,
            canReadPhoneNumber = true,
            subscriptionIdFailure = SecurityException("denied"),
            phoneFailure = IllegalStateException("radio unavailable"),
        )

        val snapshot = SubscriptionSnapshotProvider(source).capture(
            slotIndexHint = null,
            subscriptionIdHint = 9,
        )

        assertNull(snapshot.slotIndex)
        assertEquals(9, snapshot.subscriptionId)
        assertNull(snapshot.carrierName)
        assertNull(snapshot.phoneNumber)
        assertEquals(mapOf("subscription_id" to "9"), snapshot.toMetadata())
    }
}

private class FakeSubscriptionSnapshotSource(
    private val canReadSubscriptionInfo: Boolean,
    private val canReadPhoneNumber: Boolean,
    private val recordsBySubscriptionId: Map<Int, SubscriptionRecord> = emptyMap(),
    private val recordsBySlotIndex: Map<Int, SubscriptionRecord> = emptyMap(),
    private val singleRecord: SubscriptionRecord? = null,
    private val phoneNumber: String? = null,
    private val failIfRead: Boolean = false,
    private val subscriptionIdFailure: RuntimeException? = null,
    private val slotFailure: RuntimeException? = null,
    private val singleSubscriptionFailure: RuntimeException? = null,
    private val phoneFailure: RuntimeException? = null,
) : SubscriptionSnapshotSource {
    var subscriptionInfoRead = false
    var phoneNumberRead = false
    val subscriptionLookups = mutableListOf<String>()

    override fun canReadSubscriptionInfo(): Boolean = canReadSubscriptionInfo

    override fun canReadPhoneNumber(): Boolean = canReadPhoneNumber

    override fun findSubscriptionById(subscriptionId: Int): SubscriptionRecord? {
        subscriptionInfoRead = true
        check(!failIfRead) { "subscription info must not be read" }
        subscriptionLookups += "subscription:$subscriptionId"
        subscriptionIdFailure?.let { throw it }
        return recordsBySubscriptionId[subscriptionId]
    }

    override fun findSubscriptionBySlot(slotIndex: Int): SubscriptionRecord? {
        subscriptionInfoRead = true
        check(!failIfRead) { "subscription info must not be read" }
        subscriptionLookups += "slot:$slotIndex"
        slotFailure?.let { throw it }
        return recordsBySlotIndex[slotIndex]
    }

    override fun findSingleSubscription(): SubscriptionRecord? {
        subscriptionInfoRead = true
        check(!failIfRead) { "subscription info must not be read" }
        subscriptionLookups += "single"
        singleSubscriptionFailure?.let { throw it }
        return singleRecord
    }

    override fun line1Number(subscriptionId: Int): String? {
        phoneNumberRead = true
        check(!failIfRead) { "phone number must not be read" }
        phoneFailure?.let { throw it }
        return phoneNumber
    }
}
